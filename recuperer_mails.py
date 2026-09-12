#!/usr/bin/env python3
"""
recuperer_mails.py — Passerelle mail -> data/inbox_docs/.

Depuis que la veille tourne sur le serveur (12/09/2026), l'iPhone ne peut plus
déposer ses flyers dans un dossier iCloud : il les envoie par mail. Ce script
relève la boîte avant la veille et dépose les pièces jointes dans
`data/inbox_docs/`, où l'agent les traite comme avant.

COMMENT DÉPOSER : envoyer le document à l'adresse Gmail du projet avec le mot
« RADAR » dans l'objet. (L'ancienne forme « +radar » dans l'adresse reste
acceptée.)

Garde-fous — ce dossier alimente un agent, donc tout ce qui entre est une
donnée, jamais une instruction :
  - seuls les messages ENVOYÉS PAR une adresse autorisée sont pris ;
  - un message SANS pièce jointe n'est jamais touché (ni lu, ni marqué) : c'est
    ce qui protège vos propres rapports quotidiens, dont l'objet contient
    « Radar » lui aussi ;
  - seules les extensions attendues, 20 Mo par pièce au plus ;
  - noms de fichiers assainis (pas de chemin, pas de caractère exotique) ;
  - un message déjà traité n'est jamais repris : son identifiant est noté dans
    data/.mails_traites.json. On ne se fie PAS au drapeau « non lu » : un mail
    qu'on s'envoie à soi-même arrive déjà lu (constaté le 12/09/2026).

Variables d'environnement (.env) : GMAIL_SENDER, GMAIL_APP_PASSWORD, et
facultativement RADAR_INBOX_ALLOWED (expéditeurs autorisés en plus, séparés
par des virgules) et RADAR_INBOX_TAG (le mot-clé, « radar » par défaut).
"""
from __future__ import annotations

import email
import imaplib
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timedelta
from email.header import decode_header, make_header
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEST = ROOT / "data" / "inbox_docs"
REGISTRE = ROOT / "data" / ".mails_traites.json"
EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".heic", ".webp", ".gif",
              ".txt", ".docx", ".odt", ".rtf"}
TAILLE_MAX = 20 * 1024 * 1024
JOURS = 14                       # fenêtre de recherche, en jours


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


def _texte(brut: str) -> str:
    try:
        return str(make_header(decode_header(brut or "")))
    except Exception:
        return brut or ""


def _nom_sur(nom: str) -> str:
    """Nom de fichier sans chemin ni surprise : « ../../etc/passwd » est neutralisé."""
    nom = os.path.basename(_texte(nom).replace("\\", "/"))
    nom = unicodedata.normalize("NFKD", nom)
    nom = re.sub(r"[^A-Za-z0-9._ -]", "_", nom).strip(". ")
    return nom[:120] or "piece_jointe"


def _adresse(champ: str) -> str:
    return email.utils.parseaddr(champ or "")[1].lower()


def _boites(imap: imaplib.IMAP4_SSL) -> list[str]:
    """Réception + indésirables : Gmail classe volontiers là un premier envoi."""
    noms = ["INBOX"]
    try:
        typ, liste = imap.list()
        if typ == "OK":
            for br in liste or []:
                ligne = br.decode(errors="replace")
                if "\\Junk" in ligne or "\\Spam" in ligne:
                    noms.append(ligne.split(' "/" ')[-1].strip().strip('"'))
    except Exception:
        pass
    return noms


def main() -> int:
    _charger_env()
    compte = os.environ.get("GMAIL_SENDER", "").strip()
    mdp = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    if not compte or not mdp:
        print("[mailbox] GMAIL_SENDER ou GMAIL_APP_PASSWORD absent — passerelle ignorée")
        return 0

    tag = os.environ.get("RADAR_INBOX_TAG", "radar").strip()
    locale_, _, domaine = compte.partition("@")
    adresse_plus = f"{locale_}+{tag}@{domaine}".lower()

    autorisees = {compte.lower()}
    autorisees |= {a.strip().lower() for a in
                   os.environ.get("RADAR_INBOX_ALLOWED", "").split(",") if a.strip()}

    try:
        imap = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=30)
        imap.login(compte, mdp)
    except Exception as exc:                       # réseau, identifiants, quota…
        print(f"[mailbox] ATTENTION : connexion impossible ({exc})")
        return 0                                   # ne jamais faire échouer la veille

    try:
        traites = set(json.loads(REGISTRE.read_text(encoding="utf-8")))
    except Exception:
        traites = set()

    depuis = (datetime.now() - timedelta(days=JOURS)).strftime("%d-%b-%Y")
    deposes = ignores = 0
    try:
        for boite in _boites(imap):
            if imap.select(f'"{boite}"')[0] != "OK":
                continue
            # Deux façons de viser la passerelle : le mot-clé dans l'objet
            # (méthode courante) ou l'ancienne adresse « +tag ».
            numeros: list[bytes] = []
            for critere in (("SUBJECT", f'"{tag}"'), ("TO", f'"{adresse_plus}"')):
                typ, data = imap.search(None, *critere, "SINCE", depuis)
                if typ == "OK" and data and data[0]:
                    numeros += [n for n in data[0].split() if n not in numeros]

            for num in numeros:
                typ, brut = imap.fetch(num, "(RFC822)")
                if typ != "OK" or not brut or not brut[0]:
                    continue
                msg = email.message_from_bytes(brut[0][1])
                identifiant = (msg.get("Message-ID") or "").strip()
                if identifiant and identifiant in traites:
                    continue

                pieces = [p for p in msg.walk()
                          if p.get_content_maintype() != "multipart" and p.get_filename()]
                if not pieces:
                    continue        # rien à prendre : message laissé strictement intact

                exp = _adresse(msg.get("From"))
                if exp not in autorisees:
                    print(f"[mailbox] message ignoré (expéditeur non autorisé : {exp})")
                    ignores += 1
                    continue        # laissé en place, visible dans votre boîte

                objet = _texte(msg.get("Subject"))
                pris = 0
                for part in pieces:
                    nom = _nom_sur(part.get_filename())
                    if Path(nom).suffix.lower() not in EXTENSIONS:
                        print(f"[mailbox] pièce jointe refusée (type) : {nom}")
                        continue
                    contenu = part.get_payload(decode=True) or b""
                    if not contenu or len(contenu) > TAILLE_MAX:
                        print(f"[mailbox] pièce jointe refusée (taille) : {nom}")
                        continue
                    DEST.mkdir(parents=True, exist_ok=True)
                    cible = DEST / nom
                    if cible.exists():              # jamais d'écrasement silencieux
                        cible = DEST / f"{cible.stem}_{datetime.now():%H%M%S}{cible.suffix}"
                    cible.write_bytes(contenu)
                    print(f"[mailbox] déposé : {cible.name} ({len(contenu)} octets, "
                          f"de {exp}, objet « {objet[:60]} »)")
                    deposes += 1
                    pris += 1

                if pris:
                    if identifiant:
                        traites.add(identifiant)
                    imap.store(num, "+FLAGS", "\\Seen")
        REGISTRE.write_text(json.dumps(sorted(traites)[-500:]), encoding="utf-8")
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
