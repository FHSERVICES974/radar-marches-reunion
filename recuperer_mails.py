#!/usr/bin/env python3
"""
recuperer_mails.py — Passerelle mail -> data/inbox_docs/.

Depuis que la veille tourne sur le serveur (12/09/2026), l'iPhone ne peut plus
déposer ses flyers dans un dossier iCloud : il les envoie par mail. Ce script
relève la boîte avant la veille et dépose les pièces jointes dans
`data/inbox_docs/`, où l'agent les traite comme avant.

Adresse de dépôt : l'adresse Gmail du projet avec le suffixe « +radar »
(ex. moi+radar@gmail.com). Gmail la livre dans la même boîte, sans réglage.

Garde-fous (le dossier alimente un agent : tout ce qui entre est une donnée,
jamais une instruction) :
  - seuls les messages ENVOYÉS PAR une adresse autorisée sont lus ;
  - seuls les messages ADRESSÉS à l'adresse +radar sont lus ;
  - seules les extensions attendues sont acceptées, 20 Mo par pièce au plus ;
  - les noms de fichiers sont assainis (pas de chemin, pas de caractère exotique) ;
  - un message traité est marqué lu : jamais deux fois.

Variables d'environnement (.env) : GMAIL_SENDER, GMAIL_APP_PASSWORD, et
facultativement RADAR_INBOX_ALLOWED (adresses supplémentaires, séparées par
des virgules) et RADAR_INBOX_TAG (le suffixe, « radar » par défaut).
"""
from __future__ import annotations

import email
import imaplib
import os
import re
import sys
import unicodedata
from datetime import datetime
from email.header import decode_header, make_header
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEST = ROOT / "data" / "inbox_docs"
EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".heic", ".webp", ".gif",
              ".txt", ".docx", ".odt", ".rtf"}
TAILLE_MAX = 20 * 1024 * 1024


def _charger_env() -> None:
    fichier = ROOT / ".env"
    if not fichier.exists():
        return
    for ligne in fichier.read_text(encoding="utf-8").splitlines():
        ligne = ligne.strip()
        if not ligne or ligne.startswith("#") or "=" not in ligne:
            continue
        cle, _, val = ligne.partition("=")
        os.environ.setdefault(cle.strip(), val.strip().strip('"').strip("'"))


def _nom_sur(nom: str) -> str:
    """Nom de fichier sans chemin ni surprise : « ../../etc/passwd » est neutralisé."""
    nom = str(make_header(decode_header(nom or "")))
    nom = os.path.basename(nom.replace("\\", "/"))
    nom = unicodedata.normalize("NFKD", nom)
    nom = re.sub(r"[^A-Za-z0-9._ -]", "_", nom).strip(". ")
    return nom[:120] or "piece_jointe"


def _adresse(champ: str) -> str:
    return email.utils.parseaddr(champ or "")[1].lower()


def main() -> int:
    _charger_env()
    compte = os.environ.get("GMAIL_SENDER", "").strip()
    mdp = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    if not compte or not mdp:
        print("[mailbox] GMAIL_SENDER ou GMAIL_APP_PASSWORD absent — passerelle ignorée")
        return 0

    tag = os.environ.get("RADAR_INBOX_TAG", "radar").strip()
    locale_, _, domaine = compte.partition("@")
    adresse_depot = f"{locale_}+{tag}@{domaine}".lower()

    autorisees = {compte.lower()}
    autorisees |= {a.strip().lower() for a in
                   os.environ.get("RADAR_INBOX_ALLOWED", "").split(",") if a.strip()}

    try:
        imap = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=30)
        imap.login(compte, mdp)
        imap.select("INBOX")
    except Exception as exc:                      # réseau, identifiants, quota…
        print(f"[mailbox] ATTENTION : connexion impossible ({exc})")
        return 0                                  # ne jamais faire échouer la veille

    deposes = ignores = 0
    try:
        typ, data = imap.search(None, 'UNSEEN', 'TO', f'"{adresse_depot}"')
        ids = data[0].split() if typ == "OK" and data and data[0] else []
        for num in ids:
            typ, brut = imap.fetch(num, "(RFC822)")
            if typ != "OK" or not brut or not brut[0]:
                continue
            msg = email.message_from_bytes(brut[0][1])
            exp = _adresse(msg.get("From"))
            if exp not in autorisees:
                print(f"[mailbox] message ignoré (expéditeur non autorisé : {exp})")
                ignores += 1
                continue                          # laissé non lu, visible dans la boîte
            for part in msg.walk():
                if part.get_content_maintype() == "multipart":
                    continue
                brut_nom = part.get_filename()
                if not brut_nom:
                    continue
                nom = _nom_sur(brut_nom)
                if Path(nom).suffix.lower() not in EXTENSIONS:
                    print(f"[mailbox] pièce jointe refusée (type) : {nom}")
                    continue
                contenu = part.get_payload(decode=True) or b""
                if not contenu or len(contenu) > TAILLE_MAX:
                    print(f"[mailbox] pièce jointe refusée (taille) : {nom}")
                    continue
                DEST.mkdir(parents=True, exist_ok=True)
                cible = DEST / nom
                if cible.exists():                # jamais d'écrasement silencieux
                    tampon = datetime.now().strftime("%H%M%S")
                    cible = DEST / f"{cible.stem}_{tampon}{cible.suffix}"
                cible.write_bytes(contenu)
                print(f"[mailbox] déposé : {cible.name} ({len(contenu)} octets, de {exp})")
                deposes += 1
            imap.store(num, "+FLAGS", "\\Seen")
    finally:
        try:
            imap.close()
        except Exception:
            pass
        imap.logout()

    if deposes or ignores:
        print(f"[mailbox] {deposes} pièce(s) jointe(s) déposée(s), {ignores} message(s) ignoré(s)")
    else:
        print("[mailbox] aucun nouveau document")
    return 0


if __name__ == "__main__":
    sys.exit(main())
