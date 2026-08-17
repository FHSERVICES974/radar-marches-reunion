#!/usr/bin/env python3
"""
Serveur statique + webhook GitHub.
Sert index.html sur le port 5000 et expose /sync pour déclencher
un git pull automatique à chaque push sur la branche main.
"""

import base64
import datetime
import queue
import hashlib
import hmac
import html
import http.server
import json
import logging
import os
import smtplib
import subprocess
import threading
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from email.message import EmailMessage
from email.utils import formataddr

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

WEBHOOK_SECRET = os.environ.get("GITHUB_WEBHOOK_SECRET", "").encode()
BRANCH = "main"

# Intervalle (secondes) entre chaque WARNING périodique quand git est absent
_GIT_WARN_INTERVAL = 300  # 5 minutes


def _git_available() -> bool:
    """Retourne True si un dépôt git est présent dans le répertoire courant."""
    return os.path.isdir(".git")


def _periodic_git_warning():
    """Thread daemon : émet un WARNING toutes les _GIT_WARN_INTERVAL secondes
    tant que le dépôt git n'est pas initialisé."""
    while not _git_available():
        log.warning(
            "ALERTE MODE DÉGRADÉ : aucun dépôt git détecté. "
            "Le endpoint /sync est indisponible. "
            "Redémarrez le serveur avec scripts/start.sh pour rétablir l'accès à GitHub."
        )
        time.sleep(_GIT_WARN_INTERVAL)


# ---------------------------------------------------------------------------
# Notification d'alerte mode dégradé
# Variables d'environnement supportées :
#   ALERT_WEBHOOK_URL   — URL webhook (Slack / Discord / générique)
#   ALERT_EMAIL         — adresse email destinataire
#   SMTP_HOST           — serveur SMTP (défaut : localhost)
#   SMTP_PORT           — port SMTP    (défaut : 587)
#   SMTP_USER           — identifiant SMTP (optionnel)
#   SMTP_PASSWORD       — mot de passe SMTP (optionnel)
#   SMTP_FROM           — expéditeur   (défaut : noreply@localhost)
# ---------------------------------------------------------------------------

_ALERT_MESSAGE = (
    "⚠️ ALERTE MODE DÉGRADÉ\n\n"
    "Le serveur a démarré SANS dépôt git.\n"
    "Le endpoint /sync (synchronisation GitHub) est indisponible.\n\n"
    "Action requise : redémarrez le serveur via scripts/start.sh pour rétablir la synchronisation.\n"
    "Vérifiez GET /health pour surveiller l'état du service."
)


def _send_webhook_alert(url: str) -> bool:
    """Envoie une alerte via webhook (Slack / Discord / URL générique).
    Retourne True en cas de succès."""
    # Format compatible Slack et Discord (champ "text")
    payload = json.dumps({"text": _ALERT_MESSAGE}).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            log.info("Alerte webhook envoyée (HTTP %d).", resp.status)
            return True
    except urllib.error.URLError as exc:
        log.error("Échec envoi alerte webhook : %s", exc)
        return False


# ── Gabarit HTML partagé pour TOUS les emails de l'app ──────────────────────
# Charte du site : fond #f6f4ee, texte #211f1a, gris #8a8474, filets #e7e1d2,
# émeraude #0e6b52, or #a9812f, rouge-brun #93453a.

_EMAIL_TONES = {"ok": "#0e6b52", "warn": "#a9812f", "alert": "#93453a"}
_SITE_URL = "https://radar.fhservices.re"
_LOGO_PATH = os.path.join("assets", "logo_radar_marches.png")


def _email_card(tone: str, heading: str, content_html: str) -> str:
    """Une « carte » blanche à liseré coloré : ok (émeraude), warn (or),
    alert (rouge-brun). heading et content_html : HTML déjà échappé."""
    color = _EMAIL_TONES.get(tone, _EMAIL_TONES["ok"])
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="margin:0 0 16px 0"><tr><td style="background:#ffffff;'
        f'border:1px solid #e7e1d2;border-top:4px solid {color};'
        f'border-radius:12px;padding:18px 20px">'
        f'<div style="font-weight:700;color:{color};font-size:16px;'
        f'margin:0 0 8px 0">{heading}</div>'
        f'<div style="color:#211f1a;font-size:14px;line-height:1.55">'
        f'{content_html}</div>'
        f'</td></tr></table>'
    )


def _email_html(subtitle: str, cards_html: str) -> str:
    """Coquille visuelle commune à tous les emails : logo rond, marque,
    sous-titre gris, cartes, pied de page avec l'URL du site."""
    return f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f6f4ee">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
       style="background:#f6f4ee"><tr><td align="center" style="padding:28px 12px">
<table role="presentation" width="600" cellpadding="0" cellspacing="0"
       style="max-width:600px;width:100%">
<tr><td align="center" style="padding:0 0 18px 0">
  <img src="cid:radar-logo" width="64" height="64" alt="Radar des Marchés"
       style="display:block;border-radius:50%;margin:0 auto 10px auto">
  <div style="font-weight:700;font-size:19px;color:#211f1a;
       font-family:Georgia,'Times New Roman',serif">Radar des Marchés</div>
  <div style="font-size:13px;color:#8a8474;margin-top:4px">{html.escape(subtitle)}</div>
</td></tr>
<tr><td style="font-family:Arial,Helvetica,sans-serif">{cards_html}</td></tr>
<tr><td align="center" style="padding:8px 0 0 0;border-top:1px solid #e7e1d2">
  <div style="font-size:12px;color:#8a8474;padding-top:10px">radar.fhservices.re</div>
</td></tr>
</table></td></tr></table></body></html>"""


def _send_email(subject: str, body: str, recipient: str,
                html_body: str = "") -> bool:
    """Envoie un email via SMTP (mécanisme unique de l'app).
    Retourne True en cas de succès."""
    # Gmail par défaut (compte du propriétaire) — surchargeables par env vars.
    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", 587))
    smtp_user = os.environ.get("SMTP_USER", "shadowneox@gmail.com")
    smtp_password = (os.environ.get("GMAIL_APP_PASSWORD", "")
                     or os.environ.get("SMTP_PASSWORD", ""))
    smtp_from = os.environ.get("SMTP_FROM", "shadowneox@gmail.com")

    if not smtp_password:
        log.error("Échec envoi email : secret GMAIL_APP_PASSWORD manquant.")
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr(("Radar des Marchés", smtp_from))
    msg["To"] = recipient
    msg.set_content(body)  # version texte (fallback)
    if html_body:
        msg.add_alternative(html_body, subtype="html")
        # Logo inline (cid:radar-logo) attaché à la partie HTML
        try:
            with open(_LOGO_PATH, "rb") as f:
                msg.get_payload()[1].add_related(
                    f.read(), maintype="image", subtype="png",
                    cid="<radar-logo>")
        except OSError as exc:
            log.warning("Logo email introuvable (%s) — envoi sans image.", exc)

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as server:
            server.ehlo()
            if smtp_port != 25:
                server.starttls()
                server.ehlo()
            if smtp_user and smtp_password:
                server.login(smtp_user, smtp_password)
            server.send_message(msg)
        log.info("Email envoyé à %s (%s).", recipient, subject[:60])
        return True
    except Exception as exc:
        log.error("Échec envoi email : %s", exc)
        return False


def _send_email_alert(recipient: str) -> bool:
    """Alerte de démarrage en mode dégradé (compat historique)."""
    return _send_email("⚠️ Serveur démarré en mode dégradé (git absent)",
                       _ALERT_MESSAGE, recipient)


def _send_degraded_alert():
    """Envoie une notification unique lors d'un démarrage en mode dégradé.
    Tente le webhook en priorité, puis l'email si configuré."""
    webhook_url = os.environ.get("ALERT_WEBHOOK_URL", "").strip()
    alert_email = os.environ.get("ALERT_EMAIL", "").strip()

    if not webhook_url and not alert_email:
        log.info(
            "Aucune notification d'alerte configurée. "
            "Définissez ALERT_WEBHOOK_URL ou ALERT_EMAIL pour recevoir une alerte au démarrage dégradé."
        )
        return

    if webhook_url:
        _send_webhook_alert(webhook_url)

    if alert_email:
        _send_email_alert(alert_email)


def verify_signature(payload: bytes, signature_header: str) -> bool:
    """Vérifie la signature HMAC-SHA256 envoyée par GitHub."""
    if not WEBHOOK_SECRET:
        log.warning("GITHUB_WEBHOOK_SECRET non défini — vérification désactivée.")
        return True
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(WEBHOOK_SECRET, payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


# Sérialise toutes les opérations git (webhook /sync, publication admin,
# push des décisions) pour éviter les courses fetch/reset/commit/push.
_git_ops_lock = threading.Lock()


def git_pull():
    """Synchronise le working tree avec origin/main.

    Utilise git fetch + git reset --hard FETCH_HEAD plutôt que git pull,
    pour éviter l'erreur "untracked files would be overwritten by merge"
    qui survient quand le bundle de déploiement contient des fichiers
    non-trackés par git (pipeline scripts, data/, etc.).
    git reset --hard force le working tree à correspondre au remote
    sans se bloquer sur les fichiers non-trackés.
    """
    import os as _os

    if not _os.path.isdir(".git"):
        log.error(
            "ERREUR CRITIQUE : Pas de dépôt git dans ce conteneur. "
            "Le déploiement doit utiliser scripts/start.sh pour initialiser git. "
            "Vérifiez que la cible de déploiement est 'vm' dans .replit."
        )
        return

    try:
        with _git_ops_lock:
            # Étape 1 : fetch
            fetch = subprocess.run(
                ["git", "fetch", "origin", BRANCH, "--depth=1"],
                capture_output=True, text=True, timeout=60,
            )
            if fetch.returncode != 0:
                log.error("git fetch a échoué (code %d) :\n%s",
                          fetch.returncode, fetch.stderr.strip())
                return

            # Étape 2 : reset hard — force le working tree sans se bloquer
            # sur les fichiers non-trackés présents dans le bundle de déploiement
            reset = subprocess.run(
                ["git", "reset", "--hard", "FETCH_HEAD"],
                capture_output=True, text=True, timeout=30,
            )
            if reset.returncode == 0:
                log.info("Sync réussie (fetch + reset) :\n%s", reset.stdout.strip())
                _invalidate_events_cache()
            else:
                log.error("git reset --hard a échoué (code %d) :\n%s",
                          reset.returncode, reset.stderr.strip())
    except subprocess.TimeoutExpired:
        log.error("git fetch/reset a expiré après 60 secondes.")
    except Exception as exc:
        log.error("Erreur inattendue lors de la sync git : %s", exc)


# ── Le ti artisan futé — Assistant IA ─────────────────────────────────────

_ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# ── Modèles Claude — référence de configuration ────────────────────────────
# Pour changer de génération manuellement : modifiez ces deux lignes seulement.
# Le thread _model_check_loop surveille que ces modèles restent actifs et
# bascule automatiquement vers un remplaçant du même tier si l'un est retiré.
_MODEL_FAST   = "claude-haiku-4-5-20251001"   # rapide/économique — marchés du site
_MODEL_STRONG = "claude-sonnet-4-5-20250929"  # plus fort — questions admin/recherche

# Mots-clés qui identifient chaque tier de coût.
# Un modèle FAST ne peut remplacer que du FAST, STRONG que du STRONG.
_TIER_KEYWORDS: dict = {
    "FAST":   ["haiku"],
    "STRONG": ["sonnet"],
}

# Noms actifs courants — initialisés depuis les constantes, puis maintenus à jour
# par _check_models_once(). Accès protégé par _models_lock.
_active_models: dict  = {"FAST": _MODEL_FAST, "STRONG": _MODEL_STRONG}
_models_lock          = threading.Lock()
_MODEL_CHECK_INTERVAL = 24 * 3600  # vérification quotidienne


def _get_model(tier: str) -> str:
    """Retourne le nom du modèle actif pour le tier donné ('FAST' ou 'STRONG')."""
    with _models_lock:
        return _active_models[tier]


def _model_tier(model_id: str) -> str | None:
    """Classe un modèle dans son tier d'après son nom, ou None si inconnu."""
    for tier, keywords in _TIER_KEYWORDS.items():
        if any(kw in model_id for kw in keywords):
            return tier
    return None


def _fetch_model_ids() -> list:
    """Retourne la liste ordonnée des IDs de modèles actifs depuis l'API Anthropic."""
    if not _ANTHROPIC_API_KEY:
        return []
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/models",
        headers={
            "x-api-key":         _ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return [m["id"] for m in json.loads(resp.read()).get("data", [])]
    except Exception as exc:
        log.warning("Vérification modèles Anthropic : API inaccessible (%s). Modèles actuels conservés.", exc)
        return []


def _check_models_once() -> None:
    """Vérifie que les modèles actifs sont toujours disponibles ; bascule si nécessaire."""
    ids = _fetch_model_ids()
    if not ids:
        return  # avertissement déjà loggé dans _fetch_model_ids
    active_set = set(ids)
    with _models_lock:
        for tier in ("FAST", "STRONG"):
            current = _active_models[tier]
            if current in active_set:
                log.info("Modèle %s ('%s') : actif.", tier, current)
                continue
            # Modèle absent — chercher le meilleur du même tier (l'API renvoie du plus récent au plus ancien)
            candidates = [m for m in ids if _model_tier(m) == tier]
            if candidates:
                replacement = candidates[0]
                _active_models[tier] = replacement
                log.warning(
                    "MODÈLE REMPLACÉ [tier %s] : '%s' n'est plus disponible. "
                    "Basculement automatique vers '%s'. "
                    "Mettez à jour la constante _MODEL_%s dans server.py.",
                    tier, current, replacement, tier,
                )
            else:
                log.error(
                    "ALERTE MODÈLE [tier %s] : '%s' n'est plus disponible "
                    "et aucun remplaçant de même niveau n'a été trouvé. "
                    "Les appels Claude vont échouer. Mettez à jour manuellement server.py.",
                    tier, current,
                )


def _model_check_loop() -> None:
    """Thread daemon : vérifie les modèles au démarrage puis toutes les 24 h."""
    _check_models_once()
    while True:
        time.sleep(_MODEL_CHECK_INTERVAL)
        _check_models_once()


# ─────────────────────────────────────────────────────────────────────────

# Limitation : 20 messages par heure et par IP
_RATE_MAX    = 20
_RATE_WINDOW = 3600  # secondes

_rate_store: dict = {}
_rate_lock  = threading.Lock()


def _check_rate(ip: str) -> bool:
    now = time.time()
    with _rate_lock:
        ts = [t for t in _rate_store.get(ip, []) if now - t < _RATE_WINDOW]
        if len(ts) >= _RATE_MAX:
            _rate_store[ip] = ts
            return False
        ts.append(now)
        _rate_store[ip] = ts
        return True


# Cache du résumé des événements (invalidé après chaque git pull)
_events_cache: list = [None]
_events_lock  = threading.Lock()


def _load_events() -> str:
    """Lit index.html, parse le tableau EVENTS (JSON) et retourne un résumé lisible."""
    with _events_lock:
        if _events_cache[0] is not None:
            return _events_cache[0]
        try:
            with open("index.html", encoding="utf-8") as f:
                html = f.read()
            m = re.search(r"const EVENTS = (\[.+?\]);", html, re.DOTALL)
            if not m:
                log.warning("_load_events : tableau EVENTS introuvable dans index.html.")
                _events_cache[0] = ""
                return ""
            events = json.loads(m.group(1))
            _STATUS_LABELS = {
                "open":   "Candidature ouverte",
                "soon":   "À surveiller / appel à venir",
                "closed": "Clôturée",
                "full":   "Complet",
                "perm":   "Marché permanent",
            }
            lines = []
            for ev in events:
                row = [f"▸ {ev.get('name', '?')}"]
                if ev.get("zone"):     row.append(f"  Zone : {ev['zone']}")
                if ev.get("type"):     row.append(f"  Type : {ev['type']}")
                if ev.get("place"):    row.append(f"  Lieu : {ev['place']}")
                if ev.get("when"):     row.append(f"  Quand : {ev['when']}")
                status = ev.get("status", "")
                if status:             row.append(f"  Statut : {_STATUS_LABELS.get(status, status)}")
                if ev.get("deadline"): row.append(f"  Délai candidature : {ev['deadline']}")
                if ev.get("apply"):    row.append(f"  Comment candidater : {ev['apply']}")
                if ev.get("contact"):  row.append(f"  Contact : {ev['contact']}")
                lines.append("\n".join(row))
            _events_cache[0] = "\n\n".join(lines)
            log.info("Cache événements chargé (%d événements).", len(events))
        except Exception as exc:
            log.error("Erreur chargement événements : %s", exc)
            _events_cache[0] = ""
        return _events_cache[0]


def _invalidate_events_cache() -> None:
    with _events_lock:
        _events_cache[0] = None
    log.info("Cache événements invalidé.")


# Mots-clés pour détecter une question sur les événements du site
_EVT_KW = {
    "marché", "marche", "salon", "foire", "candidat", "exposant", "appel",
    "date", "délai", "deadline", "inscri", "dossier", "stand", "emplacement",
    "quand", "agenda", "organisateur", "nord", "sud", "est", "ouest",
    "saint-denis", "saint-paul", "saint-pierre", "bras-panon", "permanent",
    "mensuel", "hebdo", "trimestr", "annuel", "noël", "dipavali",
    "contact", "zone", "événement", "evenement", "répertoire", "repertoire",
    "liste", "calendrier", "prochaine", "prochain",
}


def _is_events_q(text: str) -> bool:
    tl = text.lower()
    return any(kw in tl for kw in _EVT_KW)


_SYS_EVENTS = (
    "Tu es « Le ti artisan futé », l'assistant chaleureux du site Agenda des Exposants — Artisans de La Réunion.\n"
    "Tu parles avec bienveillance, de façon simple et amicale, comme si tu aidais un ami artisan. Pas de jargon, "
    "jamais condescendant, toujours encourageant.\n\n"
    "Ta mission : répondre aux questions sur les marchés, foires, salons et appels à candidatures listés sur le site.\n"
    "Les données ci-dessous sont ta seule source de vérité pour les dates, délais, modalités et contacts.\n"
    "Si une information n'est pas dans les données, dis-le honnêtement et oriente vers l'organisateur ou la mairie.\n\n"
    "FORMAT STRICT : texte brut uniquement. Zéro astérisque, zéro dièse, zéro tiret long (—). "
    "Pour les listes, commence chaque élément par un tiret simple «- ». "
    "Sépare les idées par des retours à la ligne. 3–4 paragraphes courts maximum.\n\n"
    "LISTE DES ÉVÉNEMENTS DU SITE :\n{events}"
)

_SYS_ADMIN = (
    "Tu es « Le ti artisan futé », un assistant chaleureux qui aide les artisans et créateurs de La Réunion "
    "avec leurs démarches.\n"
    "Tu parles avec bienveillance, de façon simple et amicale. Pas de jargon, jamais condescendant, "
    "toujours encourageant.\n\n"
    "Pour les questions administratives (statut, immatriculation, cotisations, impôts, aides locales…), "
    "tu peux t'appuyer sur les informations disponibles sur ces sites officiels uniquement :\n"
    "• artisanat974.re et cma-reunion.fr (Chambre de Métiers et de l'Artisanat de La Réunion)\n"
    "• service-public.fr et entreprendre.service-public.fr (démarches nationales)\n"
    "• urssaf.fr (cotisations sociales)\n"
    "• impots.gouv.fr (fiscalité)\n"
    "• regionreunion.com (aides et subventions de la Région Réunion)\n"
    "• departement974.fr (aides et démarches du Département de La Réunion)\n\n"
    "RÈGLE ABSOLUE : Pour tout chiffre précis (taux, seuils, montants, plafonds…), indique toujours "
    "qu'il faut vérifier avec la Chambre de Métiers de La Réunion ou un comptable, car ces données "
    "changent régulièrement. Ne jamais affirmer un chiffre avec certitude.\n"
    "Si la question sort de ces domaines, oriente poliment vers la CMA ou un professionnel.\n\n"
    "FORMAT STRICT : texte brut uniquement. Zéro astérisque, zéro dièse, zéro tiret long (—). "
    "Pour les listes, commence chaque élément par un tiret simple «- ». "
    "Sépare les idées par des retours à la ligne. 3–4 paragraphes courts maximum."
)


def _claude(model: str, system: str, messages: list) -> str:
    """Appel à l'API Anthropic Claude."""
    if not _ANTHROPIC_API_KEY:
        return "Désolé, le service est momentanément indisponible."
    payload = json.dumps({
        "model": model,
        "max_tokens": 600,
        "system": system,
        "messages": messages,
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "content-type":      "application/json",
            "x-api-key":         _ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            return data["content"][0]["text"].strip()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        log.error("Anthropic HTTP %d : %s", exc.code, body[:300])
        return "Désolé, une erreur est survenue. Réessaie dans quelques instants 🙏"
    except Exception as exc:
        log.error("Anthropic error : %s", exc)
        return "Désolé, une erreur est survenue. Réessaie dans quelques instants 🙏"


# ── Vérification/complétion IA d'un candidat (admin) ────────────────────────

_VERIFY_SYS = (
    "Tu es un agent de vérification pour un agenda d'appels à candidature "
    "destiné aux artisans/créateurs exposants de La Réunion (974). "
    "Ta mission : vérifier et compléter la fiche d'UN candidat d'événement, "
    "avec une discipline stricte :\n"
    "1. Ne JAMAIS inventer une date, un lieu ou un contact. Chaque information "
    "doit être confirmée par une source réellement lue (page fournie ou "
    "résultat de recherche web que tu as consulté).\n"
    "2. Exige un signal La Réunion (974) clair : domaine .re, mention "
    "« Réunion / 974 / La Réunion », ou commune réunionnaise identifiable. "
    "Attention aux homonymes métropole (Saint-Denis 93, Saint-Paul 60…).\n"
    "3. La fiche n'est complète que si elle a une URL source réelle que tu as "
    "effectivement consultée. Sinon, elle reste incomplète.\n"
    "4. Mieux vaut une fiche incomplète honnête qu'une fiche complète douteuse.\n\n"
    "La fiche complète comporte EXACTEMENT ces 16 champs :\n"
    "name, zone (Nord/Est/Ouest/Sud/National), type, org (organisateur), "
    "place, when (période lisible), badge (court en majuscules ex. OCT), "
    "month (1-12, ou 99 si variable), dateStatus (confirmée/annuel/récurrent/"
    "probable…), status (open/soon/full/closed/perm), deadline, contact, social, "
    "url, apply (comment candidater), desc (description courte).\n\n"
    "Règle pour status, à appliquer par rapport à la DATE DU JOUR fournie "
    "dans le message :\n"
    "- open : les candidatures sont ouvertes maintenant (deadline aujourd'hui "
    "ou dans le futur, ou pas de deadline mais inscriptions en cours) ;\n"
    "- soon : les candidatures ne sont pas encore ouvertes ;\n"
    "- closed : la deadline est STRICTEMENT passée, ou la clôture est "
    "explicitement annoncée par la source ;\n"
    "- full : l'organisateur annonce explicitement que l'événement est complet "
    "ou que les places sont prises. À ne mettre QUE si la source le dit ; ne "
    "jamais le déduire d'un silence ou d'une impression. Quand full et closed "
    "pourraient tous deux s'appliquer, full PRIME : « complet » renseigne "
    "l'artisan mieux que « la date est passée » ;\n"
    "- perm : inscriptions permanentes / au fil de l'eau.\n"
    "Ne mets JAMAIS closed si la deadline est aujourd'hui ou dans le futur.\n\n"
    "Réponds UNIQUEMENT avec un objet JSON (aucun texte autour) :\n"
    '{"complete": true|false, "event": {…16 champs…} ou null, '
    '"report": "ce qui a été trouvé/vérifié, et ce qui manque ou reste non '
    'confirmé (en français, 2-5 phrases)"}\n'
    "Mets complete=true SEULEMENT si les 16 champs sont remplis à partir de "
    "sources vérifiées. Les champs social/deadline/contact peuvent être vides "
    "(\"\") s'ils n'existent pas publiquement, mais name, zone, place, when, "
    "url et desc doivent être confirmés."
)


def _fetch_page_text(url: str) -> str:
    """Télécharge une page publique et retourne son texte brut (HTML dépouillé).

    Refuse les cibles internes (anti-SSRF) : localhost, IPs privées/link-local.
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("URL non supportée")
    import ipaddress
    import socket
    for res in socket.getaddrinfo(parsed.hostname, None):
        ip = ipaddress.ip_address(res[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ValueError("adresse interne refusée")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (RadarAdmin/1.0)"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read(300_000).decode("utf-8", errors="replace")
    raw = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    raw = re.sub(r"\s+", " ", raw)
    return raw[:15_000]


def _verify_candidate_with_ai(candidate: dict, info: str) -> tuple:
    """Vérifie/complète un candidat via Claude + recherche web.

    Retourne (complete: bool, event: dict|None, report: str).
    """
    if not _ANTHROPIC_API_KEY:
        return False, None, "Service IA indisponible (clé API manquante)."

    # Récupérer le contenu des URLs collées par le propriétaire.
    pages = []
    for url in re.findall(r"https?://\S+", info)[:3]:
        try:
            pages.append(f"--- Contenu de {url} ---\n{_fetch_page_text(url)}")
        except Exception as exc:
            pages.append(f"--- {url} : téléchargement impossible ({exc}) ---")

    today_reu = datetime.datetime.now(
        datetime.timezone(datetime.timedelta(hours=4))).strftime("%d/%m/%Y")
    user_msg = (
        f"DATE DU JOUR (La Réunion) : {today_reu}\n\n"
        "Candidat actuel (JSON) :\n"
        + json.dumps(candidate, ensure_ascii=False, indent=1)
        + "\n\nInformations fournies par le propriétaire :\n" + (info or "(aucune)")
        + ("\n\n" + "\n\n".join(pages) if pages else "")
        + "\n\nVérifie et complète la fiche. Utilise la recherche web si besoin. "
          "Réponds uniquement avec le JSON demandé."
    )
    payload = json.dumps({
        "model": _get_model("STRONG"),
        "max_tokens": 3000,
        "system": _VERIFY_SYS,
        "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}],
        "messages": [{"role": "user", "content": user_msg}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "content-type":      "application/json",
            "x-api-key":         _ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=240) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        log.error("Vérification IA — Anthropic HTTP %d : %s", exc.code, body[:300])
        return False, None, f"Erreur API Anthropic (HTTP {exc.code}). Réessayez."
    except Exception as exc:
        log.error("Vérification IA : %s", exc)
        return False, None, f"Erreur lors de l'appel IA : {exc}"

    text = " ".join(b.get("text", "") for b in data.get("content", [])
                    if b.get("type") == "text").strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return False, None, "Réponse IA illisible (pas de JSON). Réessayez."
    try:
        result = json.loads(m.group(0))
    except json.JSONDecodeError:
        return False, None, "Réponse IA illisible (JSON invalide). Réessayez."

    report = str(result.get("report") or "").strip()
    ev = result.get("event") or {}
    complete = bool(result.get("complete")) and isinstance(ev, dict)
    if complete:
        missing = [f for f in _EVENT_FIELDS if f not in ev]
        core_empty = [f for f in ("name", "zone", "place", "when", "url", "desc")
                      if not str(ev.get(f, "")).strip()]
        if missing or core_empty:
            complete = False
            report = (report + f" [Contrôle serveur : champs manquants ou vides : "
                      f"{', '.join(missing + core_empty)}]").strip()
    if complete:
        ev = {f: ev.get(f, "") for f in _EVENT_FIELDS}  # 16 champs exactement
        return True, ev, report or "Fiche vérifiée et complétée."
    return False, None, report or "Vérification incomplète — aucun détail fourni par l'IA."


def _run_completion_job(key: str, candidate: dict, info: str) -> None:
    """Thread d'arrière-plan : vérifie le candidat et persiste le résultat."""
    try:
        complete, ev, report = _verify_candidate_with_ai(candidate, info)
        if complete:
            _save_completion(key, {"status": "done", "event": ev, "report": report})
            _push_completions()  # survit aux resets VM (comme les décisions)
            log.info("Complétion IA réussie : %s", ev.get("name", key))
        else:
            _save_completion(key, {"status": "failed", "report": report})
            log.info("Complétion IA incomplète (%s) : %s", key, report[:120])
    except Exception as exc:
        log.error("Complétion IA — erreur inattendue : %s", exc)
        _save_completion(key, {"status": "failed", "report": f"Erreur interne : {exc}"})


# ── Analytics — statistiques du site ─────────────────────────────────────────

_DATA_DIR       = "data"
_TRAFFIC_FILE   = os.path.join(_DATA_DIR, "traffic.json")
_QUESTIONS_FILE = os.path.join(_DATA_DIR, "chat_questions.jsonl")
_THEMES_FILE    = os.path.join(_DATA_DIR, "theme_analysis.json")
_PENDING_DIR    = "data/pending"
_DECISIONS_FILE = os.path.join(_DATA_DIR, "pending_decisions.json")

_traffic_lock   = threading.Lock()
_decisions_lock = threading.Lock()

_CONF_RANK: dict = {"Vérifié": 0, "Probable": 1, "À confirmer": 2}

# IPs uniques vues aujourd'hui (reset automatique au changement de jour)
_today_ips:      set = set()
_today_date_str: str = ""

_THEMES_INTERVAL = 7 * 24 * 3600  # analyse hebdomadaire


_REF_SOURCES = [
    ("google",    ("google.", "bing.", "yahoo.", "duckduckgo.", "qwant.", "ecosia.")),
    ("facebook",  ("facebook.com", "fb.com", "messenger.com", "m.me")),
    ("instagram", ("instagram.com", "instagr.am")),
    ("whatsapp",  ("whatsapp.com", "wa.me")),
    ("linkedin",  ("linkedin.com", "lnkd.in")),
]

# Referrer venant du site lui-même (navigation interne) — à ne pas classer
# « autre » : c'était la cause du gros paquet de visites non attribuées.
_INTERNAL_REF_HOSTS = ("radar.artisanspei.re", "radar.fhservices.re")


_UTM_SOURCES = {
    "whatsapp": "whatsapp", "wa": "whatsapp",
    "instagram": "instagram", "ig": "instagram",
    "facebook": "facebook", "fb": "facebook",
    "email": "email", "mail": "email", "newsletter": "email",
    "signature": "email", "google": "google",
}


def _categorize_referrer(referrer: str) -> str:
    """Classe l'URL de référence en une source simple."""
    if not referrer:
        return "direct"
    try:
        host = (urllib.parse.urlparse(referrer).hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        if host in _INTERNAL_REF_HOSTS or host.endswith((".replit.dev", ".replit.app", ".repl.co")):
            return "interne"
        for src, patterns in _REF_SOURCES:
            if any(p in host for p in patterns):
                return src
        return "autre"
    except Exception:
        return "direct"


def _categorize_source(referrer: str, path: str) -> str:
    """Source de trafic : utm_source (prioritaire) puis referrer en repli."""
    try:
        qs  = urllib.parse.urlparse(path).query
        utm = (urllib.parse.parse_qs(qs).get("utm_source", [""])[0]).strip().lower()
        if utm:
            return _UTM_SOURCES.get(utm, "autre")
    except Exception:
        pass
    return _categorize_referrer(referrer)


# ── Stockage persistant des statistiques (PostgreSQL) ────────────────────────
# Les stats vivaient dans data/traffic.json + data/clicks.jsonl, effacés à
# chaque publication (disque réinitialisé). Elles vivent désormais dans la
# base PostgreSQL Replit (DATABASE_URL), qui survit aux redéploiements.

_CANONICAL_HOST  = "radar.artisanspei.re"   # domaine canonique (SEO, 301)
_DB_URL          = os.environ.get("DATABASE_URL", "")
_STATS_SALT      = os.environ.get("SESSION_SECRET", "radar-stats-salt")
_STATS_RETENTION_MONTHS = 24
_stats_queue: "queue.Queue" = queue.Queue(maxsize=5000)

try:
    import psycopg2  # type: ignore
except ImportError:      # échec explicite, pas de repli silencieux
    psycopg2 = None
    log.error("psycopg2 absent — les statistiques ne seront PAS enregistrées.")


def _visitor_hash(ip: str) -> str:
    """Identifiant visiteur anonymisé : hachage salé à sens unique, jamais l'IP."""
    return hashlib.sha256((_STATS_SALT + ip).encode()).hexdigest()[:16]


def _stats_connect():
    if not psycopg2 or not _DB_URL:
        raise RuntimeError("Base de statistiques indisponible (psycopg2/DATABASE_URL)")
    conn = psycopg2.connect(_DB_URL, connect_timeout=10)
    conn.autocommit = True
    return conn


def _stats_query(sql: str, params: tuple = ()) -> list:
    """Lecture ponctuelle (connexion courte). Lève en cas d'échec — loggé par l'appelant."""
    conn = _stats_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        conn.close()


def _stats_writer_loop() -> None:
    """Thread unique d'écriture : consomme la file et insère en base.
    Reconnexion automatique ; toute perte est loggée explicitement."""
    conn = None
    backoff = 1
    while True:
        item = _stats_queue.get()
        # Retente indéfiniment avec backoff (1 s → 60 s max) : l'événement n'est
        # jamais abandonné tant que la file (5000 entrées) absorbe le trafic.
        while True:
            try:
                if conn is None or conn.closed:
                    conn = _stats_connect()
                with conn.cursor() as cur:
                    if item[0] == "pv":
                        cur.execute(
                            "INSERT INTO page_views (page, visitor_hash, referrer, ref_type, user_agent) "
                            "VALUES (%s, %s, %s, %s, %s)", item[1:])
                    elif item[0] == "cq":
                        cur.execute(
                            "INSERT INTO chat_questions (question, model_tier) "
                            "VALUES (%s, %s)", item[1:])
                    else:
                        cur.execute(
                            "INSERT INTO interactions (type, event_name, visitor_hash) "
                            "VALUES (%s, %s, %s)", item[1:])
                backoff = 1
                break
            except Exception as exc:
                conn = None
                log.error("Stats : base injoignable (%s) — nouvel essai dans %d s "
                          "(file : %d en attente).", exc, backoff, _stats_queue.qsize())
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)


def _stats_retention_loop() -> None:
    """Purge quotidienne : supprime tout ce qui dépasse 24 mois (conformité)."""
    while True:
        try:
            conn = _stats_connect()
            try:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM page_views WHERE ts < now() - interval '%s months'"
                                % _STATS_RETENTION_MONTHS)
                    pv = cur.rowcount
                    cur.execute("DELETE FROM interactions WHERE ts < now() - interval '%s months'"
                                % _STATS_RETENTION_MONTHS)
                    ix = cur.rowcount
                    cur.execute("DELETE FROM chat_questions WHERE ts < now() - interval '%s months'"
                                % _STATS_RETENTION_MONTHS)
                    if pv or ix or cur.rowcount:
                        log.info("Stats : purge rétention — %d vues, %d interactions, "
                                 "%d questions supprimées.", pv, ix, cur.rowcount)
            finally:
                conn.close()
        except Exception as exc:
            log.error("Stats : purge rétention impossible : %s", exc)
        time.sleep(24 * 3600)


def _import_legacy_stats() -> None:
    """Reprise unique des anciens fichiers locaux (traffic.json / clicks.jsonl)
    vers la base. Idempotent : ne réimporte jamais un jour déjà présent."""
    try:
        # traffic.json → traffic_daily_legacy (agrégats journaliers)
        if os.path.exists(_TRAFFIC_FILE):
            with open(_TRAFFIC_FILE, encoding="utf-8") as f:
                raw = json.load(f)
            if raw:
                conn = _stats_connect()
                try:
                    with conn.cursor() as cur:
                        for day, dd in raw.items():
                            cur.execute(
                                "INSERT INTO traffic_daily_legacy (day, views, uniques, refs) "
                                "VALUES (%s, %s, %s, %s) ON CONFLICT (day) DO NOTHING",
                                (day, dd.get("v", 0), dd.get("u", 0),
                                 json.dumps(dd.get("refs", {}))))
                    log.info("Stats : %d jour(s) de trafic historique repris depuis traffic.json.", len(raw))
                finally:
                    conn.close()
        # clicks.jsonl → interactions (une seule fois — marqueur sentinelle en
        # base, insensible aux interactions live arrivées entre-temps)
        if os.path.exists(_CLICKS_FILE):
            conn = _stats_connect()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1 FROM traffic_daily_legacy WHERE day = '1970-01-01'")
                    if cur.fetchone() is None:
                        cur.execute(
                            "INSERT INTO traffic_daily_legacy (day, views, uniques, refs) "
                            "VALUES ('1970-01-01', 0, 0, '{}') ON CONFLICT (day) DO NOTHING")
                        n = 0
                        with open(_CLICKS_FILE, encoding="utf-8") as f:
                            for line in f:
                                line = line.strip()
                                if not line:
                                    continue
                                try:
                                    e = json.loads(line)
                                    cur.execute(
                                        "INSERT INTO interactions (ts, type, event_name) "
                                        "VALUES (to_timestamp(%s), %s, %s)",
                                        (e.get("ts", 0), e.get("e", "")[:32], e.get("n", "")[:80]))
                                    n += 1
                                except Exception:
                                    pass
                        if n:
                            log.info("Stats : %d interaction(s) historiques reprises depuis clicks.jsonl.", n)
            finally:
                conn.close()
        # chat_questions.jsonl → chat_questions (une seule fois — import
        # transactionnel avec sentinelle '1970-01-02' : soit tout est repris et
        # marqué, soit rien, donc jamais de doublons même après une coupure)
        if os.path.exists(_QUESTIONS_FILE):
            conn = _stats_connect()
            conn.autocommit = False
            try:
                n = 0
                with conn.cursor() as cur:
                    cur.execute("SELECT 1 FROM traffic_daily_legacy WHERE day = '1970-01-02'")
                    if cur.fetchone() is None:
                        with open(_QUESTIONS_FILE, encoding="utf-8") as f:
                            for line in f:
                                line = line.strip()
                                if not line:
                                    continue
                                try:
                                    e = json.loads(line)
                                except Exception:
                                    continue
                                if e.get("q"):
                                    cur.execute(
                                        "INSERT INTO chat_questions (ts, question) "
                                        "VALUES (to_timestamp(%s), %s)",
                                        (e.get("ts", 0), str(e["q"])[:300]))
                                    n += 1
                        cur.execute(
                            "INSERT INTO traffic_daily_legacy (day, views, uniques, refs) "
                            "VALUES ('1970-01-02', 0, 0, '{}')")
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
            os.rename(_QUESTIONS_FILE, _QUESTIONS_FILE + ".imported")
            if n:
                log.info("Stats : %d question(s) chatbot reprises depuis le fichier local.", n)
    except Exception as exc:
        log.error("Stats : reprise de l'historique impossible : %s", exc)


def _snapshot_one_day(day: "datetime.date") -> None:
    """Calcule et enregistre (upsert, idempotent) le résumé agrégé d'une journée.

    Assurance anti-perte : même si le détail (page_views / interactions) était
    un jour perdu ou corrompu, cette ligne par jour préserve la tendance.
    """
    tz = "Indian/Reunion"
    v, u = _stats_query(
        "SELECT count(*), count(DISTINCT visitor_hash) FROM page_views "
        f"WHERE (ts AT TIME ZONE '{tz}')::date = %s", (day,))[0]
    new_v = _stats_query(
        "SELECT count(*) FROM (SELECT visitor_hash FROM page_views "
        "WHERE visitor_hash <> '' GROUP BY visitor_hash "
        f"HAVING min((ts AT TIME ZONE '{tz}')::date) = %s) t", (day,))[0][0]
    counts = dict(_stats_query(
        "SELECT type, count(*) FROM interactions "
        f"WHERE (ts AT TIME ZONE '{tz}')::date = %s GROUP BY type", (day,)))
    contact_clicks = sum(counts.get(t, 0) for t in _CONTACT_TYPES)
    wa_rows = _stats_query("SELECT count FROM wa_subscribers WHERE day = %s", (day,))
    wa = wa_rows[0][0] if wa_rows else None
    try:
        with open(os.path.join("data", "events.json"), encoding="utf-8") as f:
            published = len(json.load(f))
    except Exception:
        published = 0
    conn = _stats_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO daily_snapshot (day, views, uniques, new_visitors, "
                "returning_visitors, published_events, event_views, contact_clicks, "
                "chatbot_questions, org_submissions, whatsapp_subscribers) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (day) DO UPDATE SET views=EXCLUDED.views, "
                "uniques=EXCLUDED.uniques, new_visitors=EXCLUDED.new_visitors, "
                "returning_visitors=EXCLUDED.returning_visitors, "
                "published_events=EXCLUDED.published_events, "
                "event_views=EXCLUDED.event_views, "
                "contact_clicks=EXCLUDED.contact_clicks, "
                "chatbot_questions=EXCLUDED.chatbot_questions, "
                "org_submissions=EXCLUDED.org_submissions, "
                "whatsapp_subscribers=COALESCE(EXCLUDED.whatsapp_subscribers, "
                "daily_snapshot.whatsapp_subscribers)",
                (day, v, u, new_v, max(0, u - new_v), published,
                 counts.get("event_read", 0), contact_clicks,
                 counts.get("chat_question", 0), counts.get("org_submission", 0), wa))
    finally:
        conn.close()


def _stats_snapshot_loop() -> None:
    """Thread démon : une fois par heure, (re)calcule les 3 derniers jours révolus.

    L'upsert horaire des jours J-1..J-3 rend le rattrapage automatique après
    un redémarrage ou une panne — pas besoin d'un réveil précis à minuit.
    """
    time.sleep(120)  # laisse le serveur démarrer et la base s'initialiser
    while True:
        try:
            today_local = _stats_query(
                "SELECT (now() AT TIME ZONE 'Indian/Reunion')::date")[0][0]
            for back in (1, 2, 3):
                _snapshot_one_day(today_local - datetime.timedelta(days=back))
        except Exception as exc:
            log.error("Stats : snapshot quotidien impossible : %s", exc)
        try:
            _backfill_event_meta()
        except Exception as exc:
            log.error("event_meta : rattrapage impossible : %s", exc)
        try:
            # Hygiène : purge les interactions de test (vérifications agent).
            conn = _stats_connect()
            try:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM interactions "
                                "WHERE event_name = 'TEST-AGENT-PROD'")
                    if cur.rowcount:
                        log.info("Stats : %d interaction(s) de test supprimée(s).",
                                 cur.rowcount)
                    # Correction historique (idempotente) : les requêtes qui ne
                    # sont pas de vraies pages (favicon, icônes, sondes) ne sont
                    # plus des visites — on les retire aussi du passé, où elles
                    # sont identifiables sans ambiguïté par leur chemin ou leur
                    # User-Agent de bot (même pattern que _UA_BOT_RE).
                    # Une fois la base propre, fixed==0 et aucun recalcul n'est
                    # déclenché — le coût horaire est alors négligeable.
                    _BOT_SQL_RE = (
                        r"bot|crawl|spider|slurp|curl|wget|python-requests|"
                        r"httpx|scrapy|headless|phantom|lighthouse|pingdom|"
                        r"uptime|monitor|scan|probe|zgrab|masscan|nmap|"
                        r"go-http-client|libwww|java/|okhttp"
                    )
                    cur.execute(
                        "DELETE FROM page_views "
                        "WHERE page NOT IN ('/', '/index.html') "
                        "   OR user_agent ~* %s",
                        (_BOT_SQL_RE,)
                    )
                    fixed = cur.rowcount
            finally:
                conn.close()
            if fixed:
                log.info("Stats : %d requête(s) non-page retirées de l'historique "
                         "des visites — recalcul des résumés quotidiens.", fixed)
                today_local = _stats_query(
                    "SELECT (now() AT TIME ZONE 'Indian/Reunion')::date")[0][0]
                d = datetime.date(2026, 7, 26)   # début de l'historique en base
                while d < today_local:
                    _snapshot_one_day(d)
                    d += datetime.timedelta(days=1)
        except Exception as exc:
            log.error("Stats : purge des interactions de test impossible : %s", exc)
        try:
            # Purge historique bot-UA étendue (sentinelle '1970-01-04').
            # Supprime les visites de robots (ChatGPT-User, GPTBot, CCBot, etc.)
            # qui n'étaient pas couverts par le premier nettoyage.
            # La sentinelle est posée APRÈS le recalcul — crash-safe : si le
            # serveur s'arrête avant, la prochaine exécution horaire recommence.
            _BOT_UA_SQL = (
                r"bot|crawl|spider|slurp|curl|wget|python-requests|"
                r"httpx|scrapy|headless|phantom|lighthouse|pingdom|"
                r"uptime|monitor|scan|probe|zgrab|masscan|nmap|"
                r"go-http-client|libwww|java/|okhttp|"
                r"chatgpt-user|gptbot|ccbot|anthropic-ai|claude-web|"
                r"semrushbot|ahrefsbot|mj12bot|dotbot|petalbot|bytespider"
            )
            conn = _stats_connect()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT 1 FROM traffic_daily_legacy "
                        "WHERE day = '1970-01-04'")
                    already_done = cur.fetchone() is not None
                if not already_done:
                    with conn.cursor() as cur:
                        cur.execute(
                            "DELETE FROM page_views WHERE user_agent ~* %s",
                            (_BOT_UA_SQL,))
                        ua_purged = cur.rowcount
                    if ua_purged:
                        log.info(
                            "Stats : %d visite(s) robot supprimées de "
                            "l'historique — recalcul des résumés quotidiens.",
                            ua_purged)
                    today_local = _stats_query(
                        "SELECT (now() AT TIME ZONE 'Indian/Reunion')::date"
                    )[0][0]
                    d = datetime.date(2026, 7, 26)
                    while d < today_local:
                        _snapshot_one_day(d)
                        d += datetime.timedelta(days=1)
                    # Sentinelle posée après recalcul complet
                    with conn.cursor() as cur:
                        cur.execute(
                            "INSERT INTO traffic_daily_legacy "
                            "(day, views, uniques, refs) "
                            "VALUES ('1970-01-04', 0, 0, '{}') "
                            "ON CONFLICT (day) DO NOTHING")
                    log.info("Stats : sentinelle 1970-01-04 posée — "
                             "purge bot-UA terminée.")
            finally:
                conn.close()
        except Exception as exc:
            log.error("Stats : purge bot-UA historique impossible : %s", exc)
        time.sleep(3600)


# ── Métadonnées par événement (date de publication, date limite) ─────────────

_FR_MONTH_NUM = {"janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4,
              "mai": 5, "juin": 6, "juillet": 7, "août": 8, "aout": 8,
              "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12,
              "decembre": 12, "janv": 1, "févr": 2, "fevr": 2, "avr": 4,
              "juil": 7, "sept": 9, "oct": 10, "nov": 11, "déc": 12, "dec": 12}


def _parse_deadline_date(text: str, ref: "datetime.date | None" = None):
    """Extrait la date limite d'un texte libre.

    Sur une plage de dates (ex. « 2 juin–8 août 2026 ») retourne la DERNIÈRE
    date trouvée dans le texte, qui est la date de clôture.  Retourne None si
    aucune date explicite n'est détectable : on préfère aucune date à une date
    devinée (ex. « appel attendu à l'automne 2026 »).
    """
    if not text:
        return None
    t = text.lower()
    ref_ = ref or datetime.date.today()
    found: list[tuple[int, datetime.date]] = []   # (position, date)

    # Format numérique JJ/MM/AAAA ou JJ.MM.AAAA
    for m in re.finditer(r"\b(\d{1,2})[/.](\d{1,2})[/.](\d{4})\b", t):
        try:
            found.append((m.start(),
                          datetime.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))))
        except ValueError:
            pass

    # Format littéral « 1er|N mois [AAAA] »
    for m in re.finditer(r"\b(1er|\d{1,2})\s+([a-zéèêûôîàùc]+)\.?\s*(\d{4})?\b", t):
        day = 1 if m.group(1) == "1er" else int(m.group(1))
        mon = _FR_MONTH_NUM.get(m.group(2))
        if not mon:
            continue
        year = int(m.group(3)) if m.group(3) else ref_.year
        try:
            d = datetime.date(year, mon, day)
        except ValueError:
            continue
        if not m.group(3) and d < ref_ - datetime.timedelta(days=90):
            try:
                d = datetime.date(year + 1, mon, day)
            except ValueError:
                continue
        found.append((m.start(), d))

    if not found:
        return None
    # La dernière date dans le texte est la date de clôture (fin de plage)
    found.sort(key=lambda x: x[0])
    return found[-1][1]


def _upsert_event_meta(name: str, published_on, deadline_on) -> None:
    """Enregistre les métadonnées d'un événement. La date de publication n'est
    jamais écrasée (la première publication fait foi)."""
    conn = _stats_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO event_meta (name, published_on, deadline_on) "
                "VALUES (%s,%s,%s) ON CONFLICT (name) DO UPDATE SET "
                "deadline_on = COALESCE(EXCLUDED.deadline_on, event_meta.deadline_on)",
                (name, published_on, deadline_on))
    finally:
        conn.close()


def _backfill_event_meta() -> None:
    """Complète event_meta pour les événements sans date de publication connue.
    Sources, par fiabilité décroissante : commit git « Publier : X », première
    vue de fiche en base, sinon date du premier commit du dépôt. Idempotent."""
    try:
        with open(os.path.join("data", "events.json"), encoding="utf-8") as f:
            events = json.load(f)
    except Exception as exc:
        log.error("event_meta : lecture events.json impossible : %s", exc)
        return
    known = {r[0] for r in _stats_query("SELECT name FROM event_meta")}
    missing = [e for e in events if e.get("name") and e["name"] not in known]
    if not missing:
        return
    git_dates: dict = {}
    first_commit = None
    try:
        out = subprocess.run(
            ["git", "log", "--reverse", "--pretty=%ad|%s", "--date=short"],
            capture_output=True, text=True, timeout=30).stdout
        for line in out.splitlines():
            d, _, msg = line.partition("|")
            if first_commit is None and d:
                first_commit = d
            if msg.startswith("Publier : "):
                git_dates.setdefault(msg[len("Publier : "):].strip(), d)
    except Exception as exc:
        log.error("event_meta : git log impossible : %s", exc)
    done = 0
    for e in missing:
        name = e["name"]
        pub = git_dates.get(name)
        if pub:
            pub = datetime.date.fromisoformat(pub)
        else:
            try:
                r = _stats_query(
                    "SELECT min((ts AT TIME ZONE 'Indian/Reunion')::date) "
                    "FROM interactions WHERE event_name = %s "
                    "AND type IN ('event_view', 'event_read')",
                    (name,))
                pub = r[0][0] if r and r[0][0] else None
            except Exception:
                pub = None
        if not pub and first_commit:
            pub = datetime.date.fromisoformat(first_commit)
        if not pub:
            continue
        try:
            _upsert_event_meta(name, pub, _parse_deadline_date(e.get("deadline", ""), pub))
            done += 1
        except Exception as exc:
            log.error("event_meta : upsert « %s » impossible : %s", name, exc)
    if done:
        log.info("event_meta : %d événement(s) complété(s).", done)


_UA_BOT_RE = re.compile(
    r"bot|crawl|spider|slurp|curl|wget|python-requests|httpx|scrapy|headless|"
    r"phantom|lighthouse|pingdom|uptime|monitor|scan|probe|zgrab|masscan|nmap|"
    r"go-http-client|libwww|java/|okhttp|"
    r"chatgpt-user|gptbot|ccbot|anthropic-ai|claude-web|"
    r"semrushbot|ahrefsbot|mj12bot|dotbot|petalbot|bytespider",
    re.IGNORECASE)


def _record_visit(ip: str, referrer: str = "", path: str = "/", user_agent: str = "") -> None:
    """Enregistre une visite du site public (en base, via la file d'écriture)."""
    try:
        _stats_queue.put_nowait(("pv", urllib.parse.urlparse(path).path[:200],
                                 _visitor_hash(ip), referrer[:500],
                                 _categorize_source(referrer, path), user_agent[:300]))
    except queue.Full:
        log.error("Stats : file d'écriture pleine — visite perdue.")


def _record_question(text: str, model_tier: str = "") -> None:
    """Enregistre une question du chatbot en base (via la file d'écriture).
    Anonyme : texte + date + palier de modèle uniquement — jamais de hash ni d'IP."""
    try:
        _stats_queue.put_nowait(("cq", text[:300], model_tier[:16]))
    except queue.Full:
        log.error("Stats : file d'écriture pleine — question chatbot perdue.")


def _run_theme_analysis() -> None:
    """Demande à Claude d'analyser les thèmes des questions des 30 derniers jours."""
    if not _ANTHROPIC_API_KEY:
        return
    try:
        questions = [r[0] for r in _stats_query(
            "SELECT question FROM chat_questions "
            "WHERE ts >= now() - interval '30 days' ORDER BY ts")]
    except Exception as exc:
        log.error("_run_theme_analysis (lecture base) : %s", exc)
        return

    if len(questions) < 3:
        log.info("Analyse thèmes : moins de 3 questions disponibles, abandon.")
        return False

    log.info("Analyse thèmes : analyse de %d questions.", len(questions))
    sample = questions[:200]
    questions_text = "\n".join(f"- {q}" for q in sample)
    prompt = (
        f"Voici {len(sample)} questions posées par des artisans réunionnais "
        f"à l'assistant « Le ti artisan futé » ces 30 derniers jours :\n\n"
        f"{questions_text}\n\n"
        "Identifie 5 à 8 thèmes récurrents. Pour chaque thème donne :\n"
        "- un nom court et clair (3-5 mots)\n"
        "- le nombre de questions estimé pour ce thème\n"
        "- une question représentative (courte, mot pour mot depuis la liste)\n\n"
        "Réponds UNIQUEMENT avec ce JSON valide (aucun texte avant ou après) :\n"
        '{"themes": [{"name": "...", "count": N, "example": "..."}, ...]}'
    )
    raw = _claude(
        _get_model("STRONG"),
        "Tu es un analyste de données. Réponds uniquement avec du JSON valide, aucun texte autour.",
        [{"role": "user", "content": prompt}],
    )
    try:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            raise ValueError("Pas de JSON dans la réponse Claude")
        result = json.loads(m.group())
        result["generated_at"]  = datetime.datetime.utcnow().isoformat()
        result["total_analyzed"] = len(questions)
        result["period_days"]    = 30
        os.makedirs(_DATA_DIR, exist_ok=True)
        with open(_THEMES_FILE, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        log.info("Analyse thèmes sauvegardée (%d thèmes).", len(result.get("themes", [])))
        return True
    except Exception as exc:
        log.error("_run_theme_analysis (sauvegarde) : %s", exc)
        return False


def _theme_analysis_loop() -> None:
    """Thread daemon : analyse hebdomadaire des thèmes de questions."""
    try:
        with open(_THEMES_FILE, encoding="utf-8") as f:
            last = json.load(f)
        gen = last.get("generated_at", "")
        if gen:
            age = time.time() - datetime.datetime.fromisoformat(gen).timestamp()
            if age < _THEMES_INTERVAL:
                time.sleep(_THEMES_INTERVAL - age)
    except Exception:
        pass  # Pas de fichier existant → lancer immédiatement
    while True:
        ok = _run_theme_analysis()
        # Si analyse ignorée (< 3 questions) ou échouée → réessai dans 1h
        # Sinon → attente hebdomadaire normale
        time.sleep(3600 if not ok else _THEMES_INTERVAL)


# ── Rendu de la page admin ─────────────────────────────────────────────────


def _load_traffic_stats() -> dict:
    """Trafic depuis PostgreSQL (vues live + historique repris des fichiers)."""
    raw: dict = {}
    try:
        rows = _stats_query(
            "SELECT (ts AT TIME ZONE 'Indian/Reunion')::date AS d, "
            "count(*), count(DISTINCT visitor_hash) "
            "FROM page_views GROUP BY d")
        for d, v, u in rows:
            raw[d.isoformat()] = {"v": v, "u": u, "refs": {}}
        for d, rt, c in _stats_query(
                "SELECT (ts AT TIME ZONE 'Indian/Reunion')::date AS d, ref_type, count(*) "
                "FROM page_views GROUP BY d, ref_type"):
            raw[d.isoformat()]["refs"][rt] = c
        for d, v, u, refs in _stats_query(
                "SELECT day, views, uniques, refs FROM traffic_daily_legacy"):
            key = d.isoformat()
            dd = raw.setdefault(key, {"v": 0, "u": 0, "refs": {}})
            dd["v"] += v
            dd["u"] += u
            for k2, c in (refs or {}).items():
                dd["refs"][k2] = dd["refs"].get(k2, 0) + c
    except Exception as exc:
        log.error("Stats : lecture du trafic impossible : %s", exc)
    today = datetime.date.today()
    days = []
    last7_v = last7_u = last30_v = last30_u = 0
    total_v = sum(d.get("v", 0) for d in raw.values())
    total_u = sum(d.get("u", 0) for d in raw.values())
    refs_total: dict = {}
    for i in range(30):
        d   = today - datetime.timedelta(days=i)
        key = d.isoformat()
        dd  = raw.get(key, {"v": 0, "u": 0})
        days.append({
            "date": key, "label": d.strftime("%-d %b"),
            "v": dd.get("v", 0), "u": dd.get("u", 0),
        })
        last30_v += dd.get("v", 0)
        last30_u += dd.get("u", 0)
        if i < 7:
            last7_v += dd.get("v", 0)
            last7_u += dd.get("u", 0)
        for src, cnt in dd.get("refs", {}).items():
            refs_total[src] = refs_total.get(src, 0) + cnt
    return {
        "days": days, "last7_v": last7_v, "last7_u": last7_u,
        "last30_v": last30_v, "last30_u": last30_u,
        "total_v": total_v, "total_u": total_u, "refs": refs_total,
    }


def _load_questions_stats() -> dict:
    try:
        row = _stats_query(
            "SELECT count(*), "
            "count(*) FILTER (WHERE ts >= now() - interval '30 days') "
            "FROM chat_questions")[0]
        return {"total": row[0], "last30": row[1]}
    except Exception as exc:
        log.error("_load_questions_stats : %s", exc)
        return {"total": 0, "last30": 0}


def _load_recent_questions(limit: int = 100) -> list:
    """Retourne les dernières questions posées au chatbot : [(ts, texte)…],
    les plus récentes d'abord. Aucune donnée identifiante (ni hash, ni IP)."""
    try:
        return [(r[0].timestamp(), r[1]) for r in _stats_query(
            "SELECT ts, question FROM chat_questions "
            "ORDER BY ts DESC LIMIT %s", (int(limit),))]
    except Exception as exc:
        log.error("_load_recent_questions : %s", exc)
        return []


def _load_themes() -> dict:
    try:
        with open(_THEMES_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


# ── Propositions à valider ─────────────────────────────────────────────────

def _candidate_key(c: dict) -> str:
    """Clé stable (16 hex) identifiant un candidat de façon unique.

    Supporte les deux conventions de nommage :
      - clés préfixées (_source_url, _source_title, _confidence) — format Mac
      - clés sans préfixe (source_url, source_title, confidence)  — format legacy
    """
    ev_name = (c.get("event") or {}).get("name", "")
    src_url = c.get("_source_url") or c.get("source_url", "")
    src_ttl = c.get("_source_title") or c.get("source_title", "")
    raw = f"{ev_name}|{src_url}|{src_ttl}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _load_decisions() -> dict:
    """Charge {key: {decision, ts}} depuis le fichier de décisions persisté."""
    try:
        with _decisions_lock:
            with open(_DECISIONS_FILE, encoding="utf-8") as f:
                return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_decision(key: str, decision: str) -> None:
    """Enregistre 'published' ou 'rejected' pour un candidat (thread-safe)."""
    with _decisions_lock:
        try:
            with open(_DECISIONS_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            data = {}
        data[key] = {"decision": decision, "ts": time.time()}
        os.makedirs(_DATA_DIR, exist_ok=True)
        with open(_DECISIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


_COMPLETIONS_FILE = os.path.join(_DATA_DIR, "pending_completions.json")
_completions_lock = threading.Lock()

# Les 16 champs obligatoires d'une fiche événement complète.
_EVENT_FIELDS = ["name", "zone", "type", "org", "place", "when", "badge",
                 "month", "dateStatus", "status", "deadline", "contact",
                 "social", "url", "apply", "desc"]


def _load_completions() -> dict:
    """Charge {key: {status, event?, report?, ts}} (résultats de complétion IA)."""
    try:
        with _completions_lock:
            with open(_COMPLETIONS_FILE, encoding="utf-8") as f:
                return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_completion(key: str, entry: dict) -> None:
    """Enregistre le résultat de complétion d'un candidat (thread-safe)."""
    with _completions_lock:
        try:
            with open(_COMPLETIONS_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            data = {}
        entry["ts"] = time.time()
        data[key] = entry
        os.makedirs(_DATA_DIR, exist_ok=True)
        with open(_COMPLETIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


def _norm_evname(s: str) -> str:
    """Normalise un nom d'événement pour comparaison (dédoublonnage)."""
    s = str(s).lower().strip()
    s = re.sub(r"[—–‑]", "-", s)
    s = re.sub(r"\s+", " ", s)
    return s


def _published_name_zones() -> set:
    """Couples (nom normalisé, zone) des événements déjà dans events.json.

    events.json est versionné dans git : contrairement aux fichiers runtime,
    cette source de vérité survit aux resets de la VM de production.
    """
    try:
        with open(os.path.join("data", "events.json"), encoding="utf-8") as f:
            evs = json.load(f)
        return {(_norm_evname(e.get("name", "")), e.get("zone", "")) for e in evs}
    except Exception:
        return set()


def _push_runtime_file(relpath, commit_msg: str, label: str) -> None:
    """Commit + push un ou plusieurs fichiers runtime (str ou liste) pour
    qu'ils survivent aux resets VM.

    Non bloquant pour l'utilisateur : toute erreur est loguée, jamais fatale.
    Le token GitHub est masqué dans TOUS les chemins d'erreur.
    """
    gh_token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not gh_token or not _git_available():
        return

    def _mask(s: str) -> str:
        return str(s).replace(gh_token, "***")

    push_url = (f"https://x-access-token:{gh_token}"
                f"@github.com/FHSERVICES974/radar-marches-reunion.git")
    env = dict(os.environ)
    env.pop("GIT_ASKPASS", None)
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        paths = [relpath] if isinstance(relpath, str) else list(relpath)
        with _git_ops_lock:
            subprocess.run(["git", "add", *paths], check=True, timeout=30)
            c = subprocess.run(
                ["git", "-c", "user.email=admin@radar.re",
                 "-c", "user.name=Radar Admin",
                 "commit", "-m", commit_msg],
                capture_output=True, text=True, timeout=30,
            )
            if c.returncode != 0:
                return  # rien à committer
            ok, msg = _git_pull_for_publish()  # rebase doux (commits du Mac)
            if not ok:
                log.warning("Push %s — pull préalable échoué : %s",
                            label, _mask(msg)[:200])
            p = subprocess.run(
                ["git", "-c", "credential.helper=", "push", push_url, BRANCH],
                capture_output=True, text=True, timeout=60, env=env,
            )
            if p.returncode != 0:
                log.warning("Push %s échoué : %s",
                            label, _mask(p.stderr).strip()[:200])
    except Exception as exc:
        log.warning("Push %s : %s", label, _mask(exc)[:200])


def _push_decisions() -> None:
    _push_runtime_file("data/pending_decisions.json",
                       "Décisions propositions (Publier/Rejeter)",
                       "décisions")


def _push_completions() -> None:
    _push_runtime_file("data/pending_completions.json",
                       "Complétion IA d'un candidat (vérifié)",
                       "complétions")


# ── Soumissions organisateurs (page publique /organisateurs) ────────────────
#
# Principe : soumis ≠ publié. Une soumission atterrit dans une file de
# relecture (data/organizer_submissions.json) ; seul le propriétaire, depuis
# /admin, la publie via le MÊME chemin que les candidats IA
# (_publish_event_to_repo). Annuaire de contacts interne :
# data/organizer_contacts.json — jamais rendu sur le site public.

_SUBMISSIONS_FILE  = os.path.join(_DATA_DIR, "organizer_submissions.json")
_ORG_CONTACTS_FILE = os.path.join(_DATA_DIR, "organizer_contacts.json")
_submissions_lock  = threading.Lock()
_ORG_OWNER_EMAIL   = "shadowneox@gmail.com"

_ORG_RATE_MAX    = 5          # 5 soumissions / heure / IP
_ORG_RATE_WINDOW = 3600
_org_rate_store: dict = {}


def _check_org_rate(ip: str) -> bool:
    now = time.time()
    with _rate_lock:
        ts = [t for t in _org_rate_store.get(ip, []) if now - t < _ORG_RATE_WINDOW]
        if len(ts) >= _ORG_RATE_MAX:
            _org_rate_store[ip] = ts
            return False
        ts.append(now)
        _org_rate_store[ip] = ts
        return True


def _load_json_list(path: str) -> list:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def _save_json_list(path: str, data: list) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _push_org_files(paths: list) -> None:
    _push_runtime_file(paths, "Soumissions organisateurs (file de relecture)",
                       "soumissions organisateurs")


_FR_MONTHS = [("janv", 1), ("févr", 2), ("fevr", 2), ("mars", 3), ("avril", 4),
              ("avr", 4), ("mai", 5), ("juin", 6), ("juil", 7), ("août", 8),
              ("aout", 8), ("sept", 9), ("oct", 10), ("nov", 11), ("déc", 12),
              ("dec", 12)]
_MONTH_BADGES = {1: "JANV", 2: "FÉV", 3: "MARS", 4: "AVR", 5: "MAI", 6: "JUIN",
                 7: "JUIL", 8: "AOÛT", 9: "SEPT", 10: "OCT", 11: "NOV", 12: "DÉC"}


def _month_from_text(s: str) -> int:
    """Devine le mois (1-12) depuis un texte de date en français, sinon 99."""
    low = str(s).lower()
    for token, m in _FR_MONTHS:
        if token in low:
            return m
    return 99


def _norm_email(s: str) -> str:
    return str(s).strip().lower()


_SUBMIT_REQUIRED = ["name", "zone", "type", "org", "place", "when",
                    "links", "apply", "email", "desc",
                    "submitter_name", "submitter_phone"]
_ZONES = ["Nord", "Est", "Ouest", "Sud", "National"]


def _parse_links(raw: str) -> list:
    """Extrait les liens http(s) valides (un par ligne), max 5."""
    links = []
    for line in str(raw).splitlines():
        u = line.strip()
        if not u:
            continue
        if not u.startswith(("http://", "https://")):
            u = "https://" + u
        try:
            p = urllib.parse.urlparse(u)
            if p.scheme in ("http", "https") and p.netloc and "." in p.netloc:
                links.append(u)
        except ValueError:
            continue
    return links[:5]


def _submission_to_event(sub: dict) -> dict:
    """Construit la fiche 16 champs (même structure que les candidats IA)."""
    f = sub.get("fields", {})
    month = _month_from_text(f.get("when", ""))
    contact = f.get("email", "")
    if f.get("phone"):
        contact += " · " + f["phone"]
    ev = {k: "" for k in _EVENT_FIELDS}
    ev.update({
        "name":       f.get("name", ""),
        "zone":       f.get("zone", ""),
        "type":       f.get("type", ""),
        "org":        f.get("org", ""),
        "place":      f.get("place", ""),
        "when":       f.get("when", ""),
        "badge":      _MONTH_BADGES.get(month, ""),
        "month":      month,
        "dateStatus": "à confirmer",
        "status":     "open",
        "deadline":   f.get("deadline", ""),
        "contact":    contact,
        "social":     f.get("social", ""),
        "url":        (sub.get("links") or [""])[0],
        "apply":      f.get("apply", ""),
        "desc":       f.get("desc", ""),
    })
    return ev


def _update_org_contact(sub: dict) -> tuple:
    """Crée ou enrichit (sans écraser) le contact interne + l'annuaire orgs.json.

    Retourne (paths_modifiés: list, contact: dict).
    """
    f = sub.get("fields", {})
    email = _norm_email(f.get("email", ""))
    now_iso = datetime.datetime.now().strftime("%Y-%m-%d")
    paths = [_SUBMISSIONS_FILE.replace(os.sep, "/")]

    # 1 — Annuaire de contacts interne (privé, jamais rendu publiquement)
    contacts = _load_json_list(_ORG_CONTACTS_FILE)
    entry = next((c for c in contacts if _norm_email(c.get("email")) == email), None)
    if entry is None:
        entry = {"email": email, "name": f.get("org", ""),
                 "phone": f.get("phone", ""), "social": f.get("social", ""),
                 "first_contact": now_iso, "last_contact": now_iso,
                 "events_submitted": 1, "notes": ""}
        contacts.append(entry)
    else:
        # Enrichir uniquement les champs vides — jamais écraser
        for src, dst in (("org", "name"), ("phone", "phone"), ("social", "social")):
            if not str(entry.get(dst, "")).strip() and f.get(src):
                entry[dst] = f[src]
        entry["events_submitted"] = int(entry.get("events_submitted", 0)) + 1
        entry["last_contact"] = now_iso
    _save_json_list(_ORG_CONTACTS_FILE, contacts)
    paths.append(_ORG_CONTACTS_FILE.replace(os.sep, "/"))

    # 2 — Annuaire public orgs.json (même règle : compléter, jamais écraser)
    orgs_path = os.path.join(_DATA_DIR, "orgs.json")
    orgs = _load_json_list(orgs_path)
    org_entry = next((o for o in orgs if _norm_email(o.get("m")) == email), None)
    if org_entry is None:
        orgs.append({"n": f.get("org", ""), "m": email,
                     "t": f.get("phone", ""), "s": f.get("social", ""),
                     "w": (sub.get("links") or [""])[0]})
    else:
        for src_val, dst in ((f.get("phone", ""), "t"),
                             (f.get("social", ""), "s"),
                             ((sub.get("links") or [""])[0], "w")):
            if not str(org_entry.get(dst, "")).strip() and src_val:
                org_entry[dst] = src_val
    _save_json_list(orgs_path, orgs)
    paths.append("data/orgs.json")
    return paths, entry


_ORG_TYPES = ["Marché / Brocante", "Fête / Terroir", "Salon / Foire",
              "Fête patronale", "Marché de Noël", "Événement culturel"]


def _render_organisateurs_page(flash: str = "", form: dict = None) -> str:
    """Page publique /organisateurs — proposer un événement (file de relecture)."""
    form = form or {}

    def val(k):
        return html.escape(str(form.get(k, "")), quote=True)

    flash_html = ""
    if flash.startswith("ok"):
        flash_html = ('<div class="flash ok">✅ Merci ! Votre événement a bien été '
                      'transmis. Il sera relu par notre équipe avant toute mise en '
                      'ligne — rien n\'est publié automatiquement.</div>')
    elif flash.startswith("rate"):
        flash_html = ('<div class="flash err">⏳ Trop de soumissions depuis votre '
                      'connexion (5 max par heure). Réessayez un peu plus tard.</div>')
    elif flash.startswith("err:"):
        flash_html = f'<div class="flash err">❌ {html.escape(flash[4:])}</div>'

    zone_opts = "".join(
        f'<option value="{z}"{" selected" if form.get("zone") == z else ""}>{z}</option>'
        for z in _ZONES)
    type_opts = "".join(
        f'<option value="{t}"{" selected" if form.get("type_choice") == t else ""}>{t}</option>'
        for t in _ORG_TYPES)

    return f"""<!doctype html>
<html lang="fr"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Proposer un événement — Radar Marchés Réunion</title>
<meta name="description" content="Organisateurs : proposez votre marché, fête ou salon à La Réunion. Relecture avant publication.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,600;9..144,700&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  :root{{--bg:#f6f4ee;--panel:#fff;--ink:#211f1a;--muted:#8a8474;--line:#e7e1d2;
    --accent:#0e6b52;--accent-soft:#e7f2ed;--gold:#a9812f;--gold-soft:#f6efe0;
    --serif:'Fraunces',Georgia,serif;--sans:'Inter',-apple-system,"Segoe UI",Roboto,sans-serif}}
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:var(--sans);background:var(--bg);color:var(--ink);line-height:1.6}}
  .wrap{{max-width:760px;margin:0 auto;padding:34px 18px 60px}}
  .eyebrow{{color:var(--gold);font-weight:700;font-size:12px;letter-spacing:.14em;text-transform:uppercase}}
  h1{{font-family:var(--serif);font-weight:600;font-size:34px;letter-spacing:-.3px;margin:8px 0 10px}}
  .lede{{color:var(--muted);font-size:15.5px;max-width:56ch}}
  .notice{{margin:20px 0 26px;background:var(--gold-soft);border:1px solid #e8d9b4;border-radius:12px;
    padding:13px 16px;font-size:14px}}
  .flash{{margin:0 0 20px;border-radius:12px;padding:13px 16px;font-size:14.5px}}
  .flash.ok{{background:var(--accent-soft);border:1px solid #bfdccf}}
  .flash.err{{background:#f4e9e6;border:1px solid #e2c6bf}}
  form{{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:26px 24px;
    box-shadow:0 1px 2px rgba(30,26,15,.04),0 10px 28px rgba(30,26,15,.06)}}
  fieldset{{border:0;margin:0 0 8px}}
  legend{{font-family:var(--serif);font-size:19px;font-weight:600;color:var(--accent);margin:14px 0 12px}}
  label{{display:block;font-weight:600;font-size:13.5px;margin:14px 0 5px}}
  label .opt{{color:var(--muted);font-weight:400}}
  input,select,textarea{{width:100%;border:1px solid var(--line);border-radius:10px;background:#fdfcf9;
    padding:10px 12px;font-size:14.5px;font-family:var(--sans);color:var(--ink)}}
  input:focus,select:focus,textarea:focus{{outline:2px solid var(--accent);outline-offset:0;border-color:var(--accent)}}
  textarea{{min-height:74px;resize:vertical}}
  .hint{{font-size:12.5px;color:var(--muted);margin-top:4px}}
  .grid2{{display:grid;grid-template-columns:1fr 1fr;gap:0 16px}}
  @media(max-width:560px){{.grid2{{grid-template-columns:1fr}}}}
  .hp{{position:absolute;left:-6000px;top:-6000px;height:1px;overflow:hidden}}
  button{{margin-top:22px;width:100%;background:var(--accent);color:#fff;border:0;border-radius:12px;
    padding:14px;font-size:15.5px;font-weight:700;font-family:var(--sans);cursor:pointer}}
  button:hover{{background:#0b573f}}
  .back{{display:inline-block;margin-bottom:18px;color:var(--accent);font-weight:600;font-size:13.5px;text-decoration:none}}
  .foot{{margin-top:18px;font-size:12.5px;color:var(--muted)}}
</style></head><body><div class="wrap">
<a class="back" href="/">← Retour au radar des marchés</a>
<div class="eyebrow">Espace organisateurs</div>
<h1>Proposez votre événement</h1>
<p class="lede">Marché, fête, salon, brocante… à La Réunion ? Transmettez-nous les
informations : nous vérifions chaque proposition avant de l'ajouter au radar.</p>
<div class="notice">📋 <strong>Soumis n'est pas publié</strong> : votre proposition
rejoint une file de relecture. Rien n'apparaît sur le site sans validation manuelle.</div>
{flash_html}
<form method="post" action="/organisateurs" novalidate>
  <div class="hp" aria-hidden="true">
    <label>Ne pas remplir<input type="text" name="website" tabindex="-1" autocomplete="off"></label>
  </div>
  <fieldset><legend>L'événement</legend>
    <label>Nom de l'événement *<input name="name" required maxlength="120" value="{val('name')}"></label>
    <div class="grid2">
      <label>Zone *<select name="zone" required>
        <option value="" disabled{" selected" if not form.get("zone") else ""}>Choisir…</option>{zone_opts}
      </select></label>
      <label>Type d'événement *<select name="type_choice">
        {type_opts}<option value="Autre"{" selected" if form.get("type_choice") == "Autre" else ""}>Autre (précisez)</option>
      </select></label>
    </div>
    <label>Si « Autre » : précisez le type <span class="opt">(sinon laissez vide)</span>
      <input name="type_autre" maxlength="60" value="{val('type_autre')}"></label>
    <label>Organisateur / organisation *<input name="org" required maxlength="120" value="{val('org')}"></label>
    <label>Lieu précis *<input name="place" required maxlength="160"
      placeholder="Ex. : Jardins de la plage, Saint-Pierre" value="{val('place')}"></label>
    <div class="grid2">
      <label>Date ou période *<input name="when" required maxlength="160"
        placeholder="Ex. : 12–14 septembre 2026" value="{val('when')}"></label>
      <label>Date limite de candidature <span class="opt">(recommandé)</span>
        <input name="deadline" maxlength="160" value="{val('deadline')}"></label>
    </div>
    <label>Lien(s) vers l'événement * <span class="opt">— un par ligne</span>
      <textarea name="links" required placeholder="Page officielle, post Instagram ou Facebook…">{val('links')}</textarea>
      <div class="hint">C'est le champ clé : il nous permet de vérifier et compléter votre fiche.</div></label>
    <label>Comment candidater ? *<textarea name="apply" required maxlength="600"
      placeholder="Ex. : dossier à envoyer par email avant le 30 juin…">{val('apply')}</textarea></label>
    <label>Description courte (2-3 phrases) *<textarea name="desc" required maxlength="600">{val('desc')}</textarea></label>
  </fieldset>
  <fieldset><legend>Contact organisateur</legend>
    <div class="grid2">
      <label>Email de contact *<input type="email" name="email" required maxlength="120" value="{val('email')}"></label>
      <label>Téléphone <span class="opt">(facultatif)</span>
        <input name="phone" maxlength="40" value="{val('phone')}"></label>
    </div>
    <label>Compte réseaux sociaux <span class="opt">(facultatif — ex. @moncompte)</span>
      <input name="social" maxlength="120" value="{val('social')}"></label>
  </fieldset>
  <fieldset><legend>Vos coordonnées (personne qui soumet)</legend>
    <div class="grid2">
      <label>Votre nom *<input name="submitter_name" required maxlength="120" value="{val('submitter_name')}"></label>
      <label>Votre téléphone *<input name="submitter_phone" required maxlength="40" value="{val('submitter_phone')}"></label>
    </div>
    <div class="hint">Usage interne uniquement — jamais publié sur le site.</div>
  </fieldset>
  <button type="submit">Envoyer ma proposition</button>
</form>
<script>
document.querySelector('form[action="/organisateurs"]').addEventListener('submit', function () {{
  var b = this.querySelector('button[type=submit]');
  b.disabled = true; b.textContent = 'Envoi en cours…';
}});
</script>
<p class="foot">Vos coordonnées servent uniquement à la vérification et au suivi
de votre proposition. Aucune donnée personnelle n'est publiée sans votre accord.</p>
</div></body></html>"""


def _render_org_submissions_section() -> str:
    """Section /admin : soumissions organisateurs en attente de relecture."""
    esc = lambda s: html.escape(str(s), quote=True)  # noqa: E731
    subs = _load_json_list(_SUBMISSIONS_FILE)
    pending = [s for s in subs if s.get("status") == "pending"]
    contacts = {_norm_email(c.get("email")): c for c in _load_json_list(_ORG_CONTACTS_FILE)}

    header = ('<div class="card"><div class="card-h">📨 Soumissions organisateurs'
              f' <span style="color:#8a8474;font-weight:400">({len(pending)} en attente)</span>'
              ' — <a href="/admin/contacts.csv" style="font-size:13px">Exporter les contacts (CSV)</a></div>')
    if not pending:
        return header + ('<div class="empty-st"><span>📭</span>'
                         '<p>Aucune soumission en attente.</p>'
                         '<span class="empty-sub">Les organisateurs peuvent proposer '
                         'leurs événements sur <a href="/organisateurs">/organisateurs</a>.</span>'
                         '</div></div>')

    cards = []
    for s in pending:
        f = s.get("fields", {})
        email = _norm_email(f.get("email", ""))
        known = contacts.get(email)
        if known and int(known.get("events_submitted", 0)) > 0:
            n = int(known["events_submitted"])
            badge = (f'<span class="conf-badge" style="background:#e7f2ed;color:#0e6b52">'
                     f'✔ Organisateur connu — {n} événement{"s" if n > 1 else ""} soumis</span>')
        else:
            badge = ('<span class="conf-badge" style="background:#f6efe0;color:#a9812f">'
                     '🆕 Nouvel organisateur</span>')
        links_html = " ".join(
            f'<a href="{esc(u)}" target="_blank" rel="noopener noreferrer">{esc(u)}</a><br>'
            for u in s.get("links", []) if str(u).startswith(("http://", "https://")))
        rows = "".join(
            f"<div><strong>{lbl} :</strong> {esc(f.get(k, '')) or '<em>—</em>'}</div>"
            for lbl, k in [("Zone", "zone"), ("Type", "type"), ("Organisateur", "org"),
                           ("Lieu", "place"), ("Date/période", "when"),
                           ("Date limite", "deadline"), ("Candidature", "apply"),
                           ("Email", "email"), ("Téléphone", "phone"),
                           ("Réseaux", "social"), ("Description", "desc")])
        cards.append(f"""
<div class="prop-card">
  <div style="display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;align-items:center">
    <strong style="font-size:15px">{esc(f.get('name', '(sans nom)'))}</strong>{badge}
  </div>
  <div style="font-size:13.5px;margin-top:8px;display:grid;gap:3px">{rows}
    <div><strong>Lien(s) :</strong><br>{links_html or '<em>—</em>'}</div>
    <div><strong>Soumis par :</strong> {esc(s.get('submitter_name', ''))}
         ({esc(s.get('submitter_phone', ''))}) — {esc(str(s.get('ts', ''))[:16])}</div>
  </div>
  <form method="post" action="/admin/org-approve" style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap">
    <input type="hidden" name="id" value="{esc(s.get('id', ''))}">
    <input name="note" placeholder="Note (optionnelle)" maxlength="300"
           style="flex:1;min-width:180px;border:1px solid #e7e1d2;border-radius:8px;padding:7px 10px;font-size:13px">
    <button class="btn-pub" type="submit">✅ Valider &amp; publier</button>
    <button class="btn-rej" type="submit" formaction="/admin/org-reject">❌ Rejeter</button>
    <button class="btn-rej" type="submit" formaction="/admin/org-delete" style="opacity:.75"
            onclick="return confirm('Supprimer définitivement cette soumission ? Irréversible (aucune trace conservée).')">🗑 Supprimer</button>
  </form>
</div>""")
    return header + "".join(cards) + "</div>"


def _render_published_events_section() -> str:
    """Section /admin : événements actuellement publiés (Retirer / Corriger)."""
    esc = lambda s: html.escape(str(s), quote=True)  # noqa: E731
    try:
        with open(os.path.join("data", "events.json"), encoding="utf-8") as f:
            events = json.load(f)
    except Exception:
        events = []

    if not events:
        return ('<div class="card"><div class="card-h">📚 Événements publiés (0)</div>'
                '<div class="empty-st"><span>📭</span>'
                '<p>Aucun événement publié.</p></div></div>')

    # Lignes compactes : nom · lieu · période · statut. Le formulaire de
    # correction (16 champs) n'est PAS rendu ici : il est construit côté
    # navigateur, uniquement au clic sur « Corriger », un seul à la fois,
    # à partir du bloc JSON ci-dessous.
    status_badge = {"open":  '<span class="pub-st" style="background:#dcfce7;color:#15803d">ouvert</span>',
                    "soon":  '<span class="pub-st" style="background:#fef3c7;color:#b45309">bientôt</span>',
                    "closed":'<span class="pub-st" style="background:#f1f5f9;color:#64748b">clos</span>',
                    "full":  '<span class="pub-st" style="background:#eeebf6;color:#6b5b95">complet</span>',
                    "perm":  '<span class="pub-st" style="background:#e9eef4;color:#3a5578">permanent</span>'}
    rows = []
    for i, ev in enumerate(events):
        name  = ev.get("name", "")
        place = ev.get("place", "") or ev.get("zone", "")
        when  = ev.get("when", "")[:40]
        blob  = " ".join([name, ev.get("place", ""), ev.get("org", ""),
                          ev.get("zone", ""), ev.get("type", "")]).lower()
        rows.append(
            f'<div class="pub-row" data-i="{i}" data-search="{esc(blob)}">'
            f'<div class="pub-txt"><span class="pub-nm">{esc(name)}</span>'
            f'<span class="pub-mt">{esc(" · ".join(x for x in [place, when] if x))}</span></div>'
            f'{status_badge.get(ev.get("status", ""), "")}'
            f'<div class="pub-act">'
            f'<button type="button" class="btn-prop btn-pub pub-edit" data-i="{i}">✏️ Corriger</button>'
            f'<form method="POST" action="/admin/event-remove" class="prop-form" '
            f'onsubmit="return confirm(\'Retirer cet événement du site public ? '
            f'Il disparaîtra immédiatement du radar.\')">'
            f'<input type="hidden" name="name" value="{esc(name)}">'
            f'<button type="submit" class="btn-prop btn-rej">🗑 Retirer</button></form>'
            f'</div></div>')

    data_json = json.dumps(events, ensure_ascii=False).replace("</", "<\\/")
    return f'''
<details class="card" id="pub-ev" style="padding:0">
  <summary class="card-h" style="cursor:pointer;padding:1.25rem 1.5rem;margin:0;list-style-position:inside">
    📚 Événements publiés — {len(events)}
    <span style="color:#8a8474;font-weight:400;font-size:.78rem">(retirer / corriger)</span>
  </summary>
  <div style="padding:0 1.5rem 1.25rem">
    <input id="pub-search" type="search" placeholder="🔍 Chercher un événement (nom, lieu, organisateur)…"
           style="width:100%;border:1px solid #e2e8f0;border-radius:8px;padding:9px 12px;font-size:14px;box-sizing:border-box;margin:4px 0 12px">
    <style>
      .pub-row{{display:flex;align-items:center;gap:.6rem;padding:.5rem .2rem;border-bottom:1px solid #f1f5f9;flex-wrap:wrap}}
      .pub-txt{{flex:1;min-width:180px;display:flex;flex-direction:column;gap:.1rem}}
      .pub-nm{{font-weight:600;font-size:.85rem;color:#0f172a;line-height:1.25}}
      .pub-mt{{font-size:.73rem;color:#64748b}}
      .pub-st{{font-size:.67rem;font-weight:700;padding:.15rem .5rem;border-radius:20px;white-space:nowrap}}
      .pub-act{{display:flex;gap:.4rem;flex-shrink:0}}
      #pub-editor{{background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:.85rem 1rem;margin:.5rem 0}}
      #pub-editor label{{font-size:12px;color:#64748b;display:block;margin-top:8px}}
      #pub-editor input,#pub-editor select,#pub-editor textarea{{width:100%;border:1px solid #e2e8f0;border-radius:8px;padding:6px 9px;font-size:13px;box-sizing:border-box;font-family:inherit}}
      @media(max-width:520px){{.pub-act{{width:100%;justify-content:flex-end}}}}
    </style>
    <div id="pub-list">{"".join(rows)}</div>
    <div style="text-align:center;margin-top:.8rem">
      <button type="button" id="pub-more" class="btn-prop btn-rej">Afficher plus</button>
    </div>
  </div>
</details>
<script type="application/json" id="pub-data">{data_json}</script>
<script>
(function(){{
  var EVENTS=JSON.parse(document.getElementById("pub-data").textContent);
  var PAGE=20,shown=PAGE,rows=[].slice.call(document.querySelectorAll("#pub-list .pub-row"));
  var search=document.getElementById("pub-search"),more=document.getElementById("pub-more");
  var FIELDS=[["name","Nom"],["zone","Zone"],["type","Type"],["org","Organisateur"],
    ["place","Lieu"],["when","Date / période"],["badge","Badge (ex. JANV)"],
    ["month","Mois (1–12, 99 si inconnu)"],["dateStatus","Statut de date (confirmé / annuel / à confirmer)"],
    ["status","Statut (open / soon / full / closed / perm)"],["deadline","Date limite"],["contact","Contact"],
    ["social","Réseaux"],["url","Lien"],["apply","Comment candidater"],["desc","Description"]];
  var ZONES=["Nord","Est","Ouest","Sud","National"];
  function refresh(){{
    var q=search.value.toLowerCase().trim(),n=0;
    rows.forEach(function(r){{
      var ok=!q||r.dataset.search.indexOf(q)>=0;
      r.style.display=(ok&&n<shown)?"":"none";
      if(ok)n++;
    }});
    more.style.display=n>shown?"":"none";
  }}
  search.addEventListener("input",function(){{shown=PAGE;closeEditor();refresh();}});
  more.addEventListener("click",function(){{shown+=PAGE;refresh();}});
  function closeEditor(){{var e=document.getElementById("pub-editor");if(e)e.remove();}}
  function el(tag,attrs){{var e=document.createElement(tag);for(var k in attrs)e.setAttribute(k,attrs[k]);return e;}}
  document.getElementById("pub-list").addEventListener("click",function(ev){{
    var b=ev.target.closest(".pub-edit");if(!b)return;
    var i=+b.dataset.i,data=EVENTS[i]||{{}};
    var already=document.getElementById("pub-editor");
    closeEditor();
    if(already&&already.dataset.i==String(i))return; /* re-clic = fermer */
    var box=el("div",{{id:"pub-editor","data-i":i}});
    var f=el("form",{{method:"POST",action:"/admin/event-update"}});
    var h=el("input",{{type:"hidden",name:"orig_name"}});h.value=data.name||"";f.appendChild(h);
    FIELDS.forEach(function(fd){{
      var k=fd[0],lab=document.createElement("label");lab.textContent=fd[1];var inp;
      if(k==="zone"){{inp=document.createElement("select");inp.name="zone";
        ZONES.forEach(function(z){{var o=document.createElement("option");o.value=z;o.textContent=z;
          if((data.zone||"")===z)o.selected=true;inp.appendChild(o);}});
      }}else if(k==="desc"){{inp=el("textarea",{{name:"desc",rows:"3",maxlength:"1000"}});inp.value=data.desc||"";
      }}else{{inp=el("input",{{name:k,maxlength:"400"}});inp.value=data[k]==null?"":String(data[k]);}}
      lab.appendChild(inp);f.appendChild(lab);
    }});
    var s=el("button",{{type:"submit",class:"btn-prop btn-pub",style:"margin-top:10px"}});
    s.textContent="💾 Enregistrer la correction";f.appendChild(s);
    box.appendChild(f);
    b.closest(".pub-row").insertAdjacentElement("afterend",box);
  }});
  refresh();
}})();
</script>'''


def _load_latest_proposal() -> tuple:
    """Lit le fichier de proposition le plus récent (tri alphabétique desc).

    Retourne (filename, candidates) ou (None, []).
    """
    try:
        # Filtrer sur le préfixe est indispensable : le dossier contient aussi des
        # status_AAAA-MM-JJ.json, et « status_ » trie APRÈS « pending_MAJ_ ». Sans ce
        # filtre, le tri décroissant renvoyait toujours un fichier de statuts, qui n'a
        # pas de clé de candidats — /admin affichait donc « rien à valider » alors que
        # des propositions Vérifiées attendaient. (Constaté le 07/08/2026.)
        files = sorted(
            [fn for fn in os.listdir(_PENDING_DIR)
             if fn.startswith("pending_MAJ_") and fn.endswith(".json")],
            reverse=True,
        )
        if not files:
            return None, []
        path = os.path.join(_PENDING_DIR, files[0])
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        # Supporte "new_events_candidates" (format Mac) et "candidates" (legacy)
        candidates = data.get("new_events_candidates") or data.get("candidates", [])
        return files[0], candidates
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None, []


def _git_pull_for_publish() -> tuple:
    """Pull doux (rebase) depuis GitHub avant d'écrire les données.

    Retourne (ok: bool, message: str).
    """
    if not _git_available():
        return False, "Dépôt git absent."
    try:
        r = subprocess.run(
            ["git", "pull", "--rebase", "--autostash", "origin", BRANCH],
            capture_output=True, text=True, timeout=60,
        )
        if r.returncode != 0:
            return False, r.stderr.strip() or "git pull échoué."
        return True, r.stdout.strip()
    except subprocess.TimeoutExpired:
        return False, "git pull expiré (>60 s)."
    except Exception as exc:
        return False, str(exc)


def _publish_event_to_repo(event: dict) -> tuple:
    """Publication sérialisée : voir _publish_event_to_repo_unlocked."""
    with _git_ops_lock:
        return _publish_event_to_repo_unlocked(event)


def _publish_event_to_repo_unlocked(event: dict) -> tuple:
    """Ajoute l'événement à events.json, rebuild index.html, commit et push.

    Flux : git pull → insert + sort events.json → maj meta.json → build() →
           git add → git commit → git push.
    Retourne (ok: bool, message: str).
    """
    # 1 — Pull d'abord (intègre les commits éventuels du Mac)
    ok, msg = _git_pull_for_publish()
    if not ok:
        return False, f"Synchronisation GitHub impossible : {msg}"

    events_path = os.path.join("data", "events.json")
    meta_path   = os.path.join("data", "meta.json")

    # 2 — Charger events.json
    try:
        with open(events_path, encoding="utf-8") as f:
            events = json.load(f)
    except Exception as exc:
        return False, f"Lecture events.json impossible : {exc}"

    # 3 — Dédupliquer (nom exact, insensible à la casse)
    ev_name = event.get("name", "").strip().lower()
    if any(e.get("name", "").strip().lower() == ev_name for e in events):
        return False, f"« {event.get('name')} » existe déjà dans events.json."

    # 4 — Insérer et trier par mois puis nom
    events.append(event)
    events.sort(key=lambda e: (e.get("month", 99), e.get("name", "")))

    ev_label = event.get("name", "événement")
    ok, msg = _write_events_rebuild_and_push(events, f"Publier : {ev_label}")
    if not ok:
        return False, msg
    log.info("Publié et pushé : %s", ev_label)
    try:
        today_reu = datetime.datetime.now(
            datetime.timezone(datetime.timedelta(hours=4))).date()
        _upsert_event_meta(ev_label, today_reu,
                           _parse_deadline_date(event.get("deadline", ""), today_reu))
    except Exception as exc:
        log.error("event_meta : enregistrement publication impossible : %s", exc)
    return True, f"« {ev_label} » publié et pushé sur GitHub."


def _write_events_rebuild_and_push(events: list, commit_msg: str) -> tuple:
    """Écrit events.json, met à jour meta.json, rebuild index.html depuis le
    template figé, puis commit + push. Appelant : sous _git_ops_lock, après
    _git_pull_for_publish(). Retourne (ok, message)."""
    events_path = os.path.join("data", "events.json")
    meta_path   = os.path.join("data", "meta.json")

    # 5 — Écrire events.json
    try:
        with open(events_path, "w", encoding="utf-8") as f:
            json.dump(events, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        return False, f"Écriture events.json impossible : {exc}"

    # 6 — Mettre à jour meta.json (lastUpdate)
    try:
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
        meta["lastUpdate"] = datetime.datetime.now().strftime("%Y-%m-%d")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        log.warning("meta.json non mis à jour : %s", exc)

    # 7 — Rebuild index.html depuis template.html (le design est figé — jamais modifié)
    try:
        import importlib
        import build as _build_mod
        importlib.reload(_build_mod)
        _build_mod.build()
    except Exception as exc:
        return False, f"Rebuild index.html échoué : {exc}"

    # 8 — Vérification du token puis commit + push
    gh_token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not gh_token:
        return False, (
            "Secret GITHUB_TOKEN manquant. "
            "Ajoutez un Personal Access Token GitHub dans les secrets Replit "
            "sous la clé GITHUB_TOKEN, puis republier."
        )

    # Vérifier le token via l'API REST avant de toucher à git.
    # Évite un commit orphelin si le token est mauvais.
    try:
        import urllib.request as _ureq
        _req = _ureq.Request(
            "https://api.github.com/repos/FHSERVICES974/radar-marches-reunion",
            headers={
                "Authorization": f"token {gh_token}",
                "User-Agent": "radar-admin/1.0",
                "Accept": "application/vnd.github+json",
            },
        )
        with _ureq.urlopen(_req, timeout=10) as _r:
            _repo = json.loads(_r.read().decode())
        if not _repo.get("permissions", {}).get("push", False):
            return False, (
                "GITHUB_TOKEN valide mais sans droit d'écriture. "
                "Le token doit avoir le scope 'repo' (classic) "
                "ou 'Contents: Read & write' sur ce dépôt (fine-grained)."
            )
    except Exception as _api_err:
        _msg = str(_api_err)
        if "401" in _msg:
            return False, (
                "GITHUB_TOKEN invalide ou expiré (erreur 401). "
                "Créez un nouveau token sur https://github.com/settings/tokens "
                "et mettez-le à jour dans les secrets Replit."
            )
        if "403" in _msg:
            return False, (
                "GITHUB_TOKEN refusé par GitHub (erreur 403). "
                "Vérifiez que le token appartient au compte FHSERVICES974 "
                "et qu'il a accès à ce dépôt."
            )
        # Erreur réseau inattendue : on tente quand même le push
        log.warning("Vérification API GitHub échouée (%s) — push tenté quand même.", _msg)

    # Forme x-access-token vérifiée fonctionnelle avec ce token
    # (le header "Authorization: token …" est refusé par GitHub).
    push_url = (f"https://x-access-token:{gh_token}"
                f"@github.com/FHSERVICES974/radar-marches-reunion.git")
    try:
        subprocess.run(
            ["git", "add", "data/events.json", "data/meta.json", "index.html"],
            check=True, timeout=30,
        )
        subprocess.run(
            ["git", "-c", "user.email=admin@radar.re",
             "-c", "user.name=Radar Admin",
             "commit", "-m", commit_msg],

            check=True, timeout=30,
        )
        push_env = dict(os.environ)
        push_env.pop("GIT_ASKPASS", None)      # jamais replit-git-askpass
        push_env["GIT_TERMINAL_PROMPT"] = "0"  # échec propre au lieu d'un prompt
        push = subprocess.run(
            ["git", "-c", "credential.helper=",
             "push", push_url, BRANCH],
            capture_output=True, text=True, timeout=60, env=push_env,
        )
        if push.returncode != 0:
            err = push.stderr.replace(gh_token, "***").strip()
            return False, f"git push échoué : {err}"
    except subprocess.CalledProcessError as exc:
        return False, f"Opération git échouée : {exc}"
    except subprocess.TimeoutExpired:
        return False, "git push expiré (>60 s)."

    return True, "Modifications poussées sur GitHub."


def _remove_event_from_repo(name: str) -> tuple:
    """Retire un événement publié (par nom exact) : events.json → rebuild →
    push. Retourne (ok, message)."""
    with _git_ops_lock:
        ok, msg = _git_pull_for_publish()
        if not ok:
            return False, f"Synchronisation GitHub impossible : {msg}"
        events_path = os.path.join("data", "events.json")
        try:
            with open(events_path, encoding="utf-8") as f:
                events = json.load(f)
        except Exception as exc:
            return False, f"Lecture events.json impossible : {exc}"
        target = name.strip().lower()
        kept = [e for e in events
                if e.get("name", "").strip().lower() != target]
        if len(kept) == len(events):
            return False, f"« {name} » introuvable dans events.json."
        ok, msg = _write_events_rebuild_and_push(kept, f"Retirer : {name}")
        if not ok:
            return False, msg
        log.info("Événement retiré et pushé : %s", name)
        return True, f"« {name} » retiré du site public."


def _update_event_in_repo(orig_name: str, event: dict) -> tuple:
    """Remplace la fiche d'un événement publié (repéré par son nom d'origine)
    par la fiche corrigée : events.json → rebuild → push."""
    with _git_ops_lock:
        ok, msg = _git_pull_for_publish()
        if not ok:
            return False, f"Synchronisation GitHub impossible : {msg}"
        events_path = os.path.join("data", "events.json")
        try:
            with open(events_path, encoding="utf-8") as f:
                events = json.load(f)
        except Exception as exc:
            return False, f"Lecture events.json impossible : {exc}"
        target = orig_name.strip().lower()
        idx = next((i for i, e in enumerate(events)
                    if e.get("name", "").strip().lower() == target), None)
        if idx is None:
            return False, f"« {orig_name} » introuvable dans events.json."
        # Nouveau nom déjà pris par un AUTRE événement ?
        new_name = event.get("name", "").strip().lower()
        if any(i != idx and e.get("name", "").strip().lower() == new_name
               for i, e in enumerate(events)):
            return False, f"Un autre événement s'appelle déjà « {event.get('name')} »."
        events[idx] = event
        events.sort(key=lambda e: (e.get("month", 99), e.get("name", "")))
        ok, msg = _write_events_rebuild_and_push(
            events, f"Corriger : {event.get('name', orig_name)}")
        if not ok:
            return False, msg
        log.info("Événement corrigé et pushé : %s", orig_name)
        return True, f"« {event.get('name', orig_name)} » corrigé et publié."


def _load_field_updates() -> list:
    """Corrections de fiches DÉJÀ en ligne, proposées par la veille du jour.

    Canal `field_updates` : le pipeline savait créer un événement et changer un
    statut, jamais corriger les champs d'une fiche existante. Les corrections
    vérifiées dormaient dans un champ que personne ne lisait.
    """
    try:
        files = sorted(
            [fn for fn in os.listdir(_PENDING_DIR)
             if fn.startswith("pending_MAJ_") and fn.endswith(".json")],
            reverse=True,
        )
        if not files:
            return []
        with open(os.path.join(_PENDING_DIR, files[0]), encoding="utf-8") as f:
            return json.load(f).get("field_updates", []) or []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def _field_update_key(u: dict) -> str:
    """Clé stable d'une correction : nom + source + contenu exact des changements."""
    raw = (f"{u.get('event_name','')}|{u.get('_source_url','')}|"
           f"{json.dumps(u.get('changes', {}), sort_keys=True, ensure_ascii=False)}")
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _render_field_updates_section(dev_mode: bool) -> str:
    """Section « Corrections de fiches en ligne » : avant -> après, puis validation."""
    updates = [u for u in _load_field_updates() if u.get("changes")]
    if not updates:
        return ""
    decisions = _load_decisions()
    # _load_events() renvoie un résumé TEXTE (pour le chat) : on lit le fichier.
    try:
        with open(os.path.join("data", "events.json"), encoding="utf-8") as f:
            events = {e.get("name"): e for e in json.load(f)}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        events = {}

    cards = []
    for u in updates:
        key = _field_update_key(u)
        if key in decisions:
            continue
        nom = u.get("event_name", "")
        actuel = events.get(nom)
        if actuel is None:
            cards.append(
                f'<div class="prop-card"><div class="prop-name">{html.escape(nom)}</div>'
                f'<div class="prop-meta" style="color:#b91c1c">Fiche introuvable dans '
                f'events.json — correction inapplicable.</div></div>')
            continue

        lignes = []
        for champ, apres in (u.get("changes") or {}).items():
            avant = actuel.get(champ, "")
            if avant == apres:
                continue
            lignes.append(
                f'<div style="margin:.35rem 0"><b>{html.escape(champ)}</b><br>'
                f'<span style="color:#b91c1c;text-decoration:line-through">'
                f'{html.escape(str(avant)[:160]) or "(vide)"}</span><br>'
                f'<span style="color:#15803d">{html.escape(str(apres)[:160])}</span></div>')
        if not lignes:
            continue

        raison = html.escape(u.get("raison", ""))
        src = u.get("_source_url", "")
        src_html = (f'<a href="{html.escape(src)}" target="_blank" rel="noopener">source</a>'
                    if src else "<i>pas de source</i>")
        cards.append(
            f'<div class="prop-card">'
            f'<div class="prop-top"><div class="prop-name">{html.escape(nom)}</div></div>'
            f'<div class="prop-meta">{raison} · {src_html}</div>'
            f'{"".join(lignes)}'
            f'<div class="prop-actions">'
            f'<form method="POST" action="/admin/apply-update" class="prop-form">'
            f'<input type="hidden" name="key" value="{key}">'
            f'<button type="submit" class="btn-prop btn-pub">✓ Appliquer</button></form>'
            f'<form method="POST" action="/admin/reject" class="prop-form">'
            f'<input type="hidden" name="key" value="{key}">'
            f'<button type="submit" class="btn-prop btn-rej">✕ Rejeter</button></form>'
            f'</div></div>')

    if not cards:
        return ""
    return (
        f'<section class="card"><h2>Corrections de fiches en ligne '
        f'({len(cards)})</h2>'
        f'<p class="hint">Fiches déjà publiées que la veille a trouvées fausses ou '
        f'incomplètes. Le rouge est la valeur actuelle, le vert la valeur proposée.</p>'
        f'{"".join(cards)}</section>')


def _render_proposals_section(dev_mode: bool) -> str:  # noqa: PLR0912,PLR0915
    """Génère la section 'Propositions à valider' du dashboard admin."""
    filename, candidates = _load_latest_proposal()
    decisions = _load_decisions()

    if not candidates:
        hint = ("Les fichiers de veille apparaissent ici dès qu'ils arrivent via GitHub Sync."
                if not filename else "Le fichier ne contient aucun candidat.")
        return (
            '<div class="card">'
            '<div class="card-h">📥 Propositions à valider</div>'
            '<div class="empty-st"><span>✅</span>'
            '<p>Aucune proposition en attente.</p>'
            f'<span class="empty-sub">{hint}</span>'
            '</div></div>'
        )

    # Filtrer les déjà traités, trier par confiance
    pending = [c for c in candidates if _candidate_key(c) not in decisions]

    # Appliquer les complétions IA validées (overlay local).
    # La clé d'origine est figée AVANT la fusion : _candidate_key dépend de
    # event.name, qui peut changer après complétion.
    completions = _load_completions()
    for c in pending:
        orig_key = _candidate_key(c)
        c["_orig_key"] = orig_key
        comp = completions.get(orig_key)
        if comp and comp.get("status") == "done" and comp.get("event"):
            c["event"] = comp["event"]
            c["_confidence"] = "Vérifié"

    # Dédoublonnage durable : masquer les candidats dont l'événement figure
    # déjà dans events.json (survit aux resets VM, contrairement aux décisions).
    published = _published_name_zones()
    pending = [
        c for c in pending
        if not (c.get("event") or {}).get("name")
        or (_norm_evname(c["event"]["name"]), c["event"].get("zone", "")) not in published
    ]

    if not pending:
        return (
            '<div class="card">'
            '<div class="card-h">📥 Propositions à valider</div>'
            '<div class="empty-st"><span>✅</span>'
            '<p>Tout est traité — file vide.</p>'
            '<span class="empty-sub">Nouvelle veille attendue demain.</span>'
            '</div></div>'
        )

    pending.sort(key=lambda c: _CONF_RANK.get(c.get("_confidence") or c.get("confidence", "À confirmer"), 3))

    # Compteurs
    n_pub = sum(1 for c in pending if (c.get("_confidence") or c.get("confidence")) == "Vérifié" and c.get("event"))
    n_chk = len(pending) - n_pub
    counts = []
    if n_pub:
        s = "s" if n_pub > 1 else ""
        counts.append(f'<span class="prop-cnt-pub">{n_pub} prête{s} à publier</span>')
    if n_chk:
        counts.append(f'<span class="prop-cnt-chk">{n_chk} à vérifier</span>')
    count_html = "&nbsp;·&nbsp;".join(counts)

    label   = filename.replace(".json", "").replace("propositions_", "").replace("_", "\u00a0")
    from_tag = f'<span class="prop-from">— {label}</span>' if filename else ""

    # Cartes
    cards = []
    esc = lambda s: (str(s).replace("&", "&amp;").replace("<", "&lt;")  # noqa: E731
                     .replace(">", "&gt;").replace('"', "&quot;"))
    for c in pending:
        key     = c.get("_orig_key") or _candidate_key(c)
        conf    = c.get("_confidence") or c.get("confidence", "À confirmer")
        ev      = c.get("event") or {}
        has_ev  = bool(ev and ev.get("name"))
        name    = esc(ev.get("name") or c.get("_source_title") or c.get("source_title") or "—")
        place   = esc(ev.get("place", ""))
        when    = esc(ev.get("when", ""))
        dl      = esc(ev.get("deadline", ""))
        notes   = esc(c.get("notes", ""))
        # Lien de la carte : l'url de la fiche actuelle (celle qui sera
        # publiée, y compris après re-vérification IA), sinon la source brute.
        src_url = (ev.get("url") or c.get("_source_url")
                   or c.get("source_url", "#"))
        if not src_url.startswith(("http://", "https://")):
            src_url = "#"
        src_url = esc(src_url)
        # Libellé : l'url de la fiche si c'est elle qui est affichée (sinon on
        # montrerait un ancien titre de page avec un lien devenu différent).
        if ev.get("url"):
            src_ttl = esc(ev["url"][:55])
        else:
            src_ttl = esc((c.get("_source_title") or c.get("source_title") or src_url)[:55])

        if conf == "Vérifié":
            badge = '<span class="conf-badge conf-vert">✓ Vérifié</span>'
        elif conf == "Probable":
            badge = '<span class="conf-badge conf-amb">~ Probable</span>'
        else:
            badge = '<span class="conf-badge conf-grey">? À confirmer</span>'

        meta_items = []
        if place: meta_items.append(f"📍 {place}")
        if when:  meta_items.append(f"📅 {when}")
        if dl:    meta_items.append(f"⏰ {dl[:70]}")
        meta_html = "".join(f'<span class="prop-meta-item">{x}</span>' for x in meta_items)

        notes_html = (f'<div class="prop-notes">{notes[:200]}</div>'
                      if notes and not has_ev else "")

        pub_btn = (
            f'<form method="POST" action="/admin/publish" class="prop-form">'
            f'<input type="hidden" name="key" value="{key}">'
            f'<button type="submit" class="btn-prop btn-pub">📤 Publier</button>'
            f'</form>'
        ) if has_ev else ""

        rej_btn = (
            f'<form method="POST" action="/admin/reject" class="prop-form">'
            f'<input type="hidden" name="key" value="{key}">'
            f'<button type="submit" class="btn-prop btn-rej">✕ Rejeter</button>'
            f'</form>'
        )

        del_btn = (
            f'<form method="POST" action="/admin/delete-proposal" class="prop-form" '
            f'onsubmit="return confirm(\'Supprimer définitivement cette proposition ? '
            f'Irréversible (aucune trace conservée).\')">'
            f'<input type="hidden" name="key" value="{key}">'
            f'<button type="submit" class="btn-prop btn-rej" '
            f'style="opacity:.75">🗑 Supprimer</button>'
            f'</form>'
        )

        # Bloc « compléter » : candidats sans fiche complète, et fiches déjà
        # « Vérifié » (corriger une URL source erronée et relancer l'IA).
        complete_html = ""
        if not has_ev or conf == "Vérifié":
            comp = completions.get(key)
            if comp and comp.get("status") == "running":
                complete_html = ('<div class="prop-running">⏳ Vérification IA en cours… '
                                 'rechargez la page dans une minute.</div>')
            else:
                report_html = ""
                if comp and comp.get("status") == "failed" and comp.get("report"):
                    rep = (comp["report"].replace("&", "&amp;")
                           .replace("<", "&lt;").replace(">", "&gt;"))
                    report_html = f'<div class="prop-report">🔎 {rep}</div>'
                complete_html = (
                    f'{report_html}'
                    f'<form method="POST" action="/admin/complete" class="prop-complete">'
                    f'<input type="hidden" name="key" value="{key}">'
                    f'<input type="text" name="info" class="inp-comp" maxlength="600" '
                    f'placeholder="URL de l\'annonce officielle, contact, date confirmée…">'
                    f'<button type="submit" class="btn-prop btn-comp">🔎 Vérifier et compléter</button>'
                    f'</form>'
                )

        cards.append(
            f'<div class="prop-card">'
            f'<div class="prop-top">{badge}<div class="prop-name">{name}</div></div>'
            f'<div class="prop-meta">{meta_html}</div>'
            f'{notes_html}'
            f'{complete_html}'
            f'<div class="prop-foot">'
            f'<a href="{src_url}" target="_blank" rel="noopener" class="prop-src">🔗 {src_ttl}</a>'
            f'<div class="prop-actions">{pub_btn}{rej_btn}{del_btn}</div>'
            f'</div></div>'
        )

    return (
        f'<div class="card">'
        f'<div class="card-h">📥 Propositions à valider {from_tag}</div>'
        f'<div class="prop-summary">{count_html}</div>'
        f'<div class="prop-list">' + "\n".join(cards) + '</div>'
        f'</div>'
    )


_CLICKS_FILE  = os.path.join(_DATA_DIR, "clicks.jsonl")
_clicks_lock  = threading.Lock()


def _record_click(event: str, name: str = "", visitor: str = "") -> None:
    """Enregistre une interaction (en base, via la file d'écriture)."""
    try:
        _stats_queue.put_nowait(("ix", event[:32], name[:80], visitor))
    except queue.Full:
        log.error("Stats : file d'écriture pleine — interaction perdue.")


# Types d'interaction comptés comme « clic contact » ('candidater' = ancien nom
# des clics mailto, conservé pour l'historique déjà en base).
_CONTACT_TYPES = ("candidater", "contact_email", "contact_phone",
                  "contact_social", "contact_url")


def _load_clicks_stats() -> dict:
    """Statistiques d'interactions des 30 derniers jours (PostgreSQL).
    L'indicateur event_read est dédupliqué : on compte les paires
    (visitor_hash, event_name) distinctes, pas les événements bruts."""
    totals: dict = {"chatbot_open": 0, "candidater": 0, "event_read": 0,
                    "signup_whatsapp": 0, "signup_email": 0,
                    "contact_email": 0, "contact_phone": 0,
                    "contact_social": 0, "contact_url": 0}
    top_events: dict = {}
    top_cand: dict = {}
    try:
        for ev, c in _stats_query(
                "SELECT type, count(*) FROM interactions "
                "WHERE ts >= now() - interval '30 days' GROUP BY type"):
            if ev in totals and ev != "event_read":
                totals[ev] = c
        # event_read : compter les paires (visiteur, fiche) distinctes pour
        # ne pas gonfler le chiffre avec les multi-visites du même visiteur.
        r = _stats_query(
            "SELECT count(DISTINCT (visitor_hash, event_name)) FROM interactions "
            "WHERE type = 'event_read' AND event_name <> '' "
            "AND visitor_hash <> '' AND ts >= now() - interval '30 days'")
        totals["event_read"] = r[0][0] if r else 0
        for ev, name, c in _stats_query(
                "SELECT type, event_name, count(DISTINCT visitor_hash) "
                "FROM interactions "
                "WHERE ts >= now() - interval '30 days' AND event_name <> '' "
                "AND visitor_hash <> '' AND type = ANY(%s) "
                "GROUP BY type, event_name",
                (list(("event_read",) + _CONTACT_TYPES),)):
            if ev == "event_read":
                top_events[name] = top_events.get(name, 0) + c
            else:
                top_cand[name] = top_cand.get(name, 0) + c
    except Exception as exc:
        log.error("Stats : lecture des interactions impossible : %s", exc)
    totals["contacts"] = sum(totals[t] for t in _CONTACT_TYPES)
    return {
        **totals,
        "top_events": sorted(top_events.items(), key=lambda x: x[1], reverse=True)[:8],
        "top_cand":   sorted(top_cand.items(),   key=lambda x: x[1], reverse=True)[:5],
    }


def _load_event_stats(days: int = 30) -> list:
    """Stats par événement : vues de fiche, visiteurs uniques, clics contact."""
    try:
        days = max(1, min(int(days), 730))
        rows = _stats_query(
            "SELECT event_name, "
            "count(*) FILTER (WHERE type = 'event_read'), "
            "count(DISTINCT visitor_hash) FILTER (WHERE type = 'event_read' AND visitor_hash <> ''), "
            "count(*) FILTER (WHERE type = ANY(%s)) "
            "FROM interactions "
            "WHERE event_name <> '' AND ts >= now() - make_interval(days => %s) "
            "GROUP BY event_name ORDER BY 2 DESC, 4 DESC", (list(_CONTACT_TYPES), days))
        return [{"name": r[0], "views": r[1], "uniq": r[2], "contacts": r[3]} for r in rows]
    except Exception as exc:
        log.error("Stats : lecture par événement impossible : %s", exc)
        return []


# ── Session admin (Replit Auth PKCE flow) ────────────────────────────────────

_SESSION_SECRET  = os.environ.get("SESSION_SECRET", "fallback-dev-secret")
_SESSION_COOKIE  = "radar_admin_sid"
_SESSION_TTL     = 12 * 3600   # 12 h


def _make_session_token(username: str) -> str:
    """Crée un token de session signé avec SESSION_SECRET (HMAC-SHA256)."""
    ts      = str(int(time.time()))
    payload = f"{username}:{ts}"
    sig     = hmac.new(_SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    return base64.urlsafe_b64encode(f"{payload}:{sig}".encode()).decode()


def _verify_session_token(token: str) -> str | None:
    """Vérifie le token ; retourne le username si valide et non expiré, sinon None."""
    try:
        decoded = base64.urlsafe_b64decode(token + "==").decode()
        username, ts_str, sig = decoded.rsplit(":", 2)
        if time.time() - int(ts_str) > _SESSION_TTL:
            return None
        expected = hmac.new(_SESSION_SECRET.encode(), f"{username}:{ts_str}".encode(),
                            hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(sig, expected):
            return None
        return username
    except Exception:
        return None


def _get_session_cookie(headers) -> str | None:
    """Extrait la valeur du cookie de session depuis les headers HTTP."""
    for part in headers.get("Cookie", "").split(";"):
        part = part.strip()
        if part.startswith(f"{_SESSION_COOKIE}="):
            return part[len(f"{_SESSION_COOKIE}="):]
    return None


def _parse_replit_auth_response(raw: str) -> dict:
    """Décode le authResponse renvoyé par Replit (base64url → JSON)."""
    try:
        # Ajoute le padding manquant puis décode
        padded = raw + "=" * (-len(raw) % 4)
        return json.loads(base64.urlsafe_b64decode(padded).decode())
    except Exception:
        return {}


def _render_auth_required(error: str = "") -> str:
    err_html = f'<p class="err">{error}</p>' if error else ""
    return f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Accès restreint</title>
<style>
  *{{box-sizing:border-box}}
  body{{font-family:system-ui,sans-serif;background:#f9fafb;display:flex;
       align-items:center;justify-content:center;min-height:100vh;margin:0}}
  .card{{background:#fff;border:1px solid #e5e7eb;border-radius:12px;
         padding:2.5rem 2rem;max-width:380px;width:90%;text-align:center;
         box-shadow:0 2px 8px rgba(0,0,0,.06)}}
  h1{{font-size:1.1rem;color:#111827;margin:0 0 .4rem}}
  p{{color:#6b7280;font-size:.875rem;line-height:1.5;margin:0 0 1.2rem}}
  .err{{color:#dc2626;background:#fef2f2;border:1px solid #fecaca;border-radius:6px;
        padding:.5rem .8rem;font-size:.85rem;margin-bottom:1rem;text-align:left}}
  input[type=password]{{width:100%;padding:.6rem .8rem;border:1px solid #d1d5db;
    border-radius:8px;font-size:.9rem;margin-bottom:.9rem;outline:none}}
  input[type=password]:focus{{border-color:#2563eb;box-shadow:0 0 0 3px rgba(37,99,235,.15)}}
  .btn{{width:100%;background:#2563eb;color:#fff;padding:.65rem;border-radius:8px;
        font-size:.875rem;font-weight:500;cursor:pointer;border:none}}
  .btn:hover{{background:#1d4ed8}}
</style></head>
<body>
  <div class="card">
    <div style="font-size:2rem;margin-bottom:.6rem">🔒</div>
    <h1>Espace propriétaire</h1>
    <p>Entrez le mot de passe pour accéder aux statistiques.</p>
    {err_html}
    <form method="POST" action="/admin/login">
      <input type="password" name="password" placeholder="Mot de passe" autofocus required>
      <button class="btn" type="submit">Accéder</button>
    </form>
  </div>
</body></html>"""


def _render_event_stats_section() -> str:
    """Section admin : statistiques par événement (30 derniers jours)."""
    rows = _load_event_stats(30)
    if not rows:
        body = '<tr><td class="nd" colspan="5">Aucune donnée sur la période.</td></tr>'
    else:
        body = ""
        for r in rows[:40]:
            n  = html.escape(r["name"])
            qn = urllib.parse.quote(r["name"], safe="")
            body += (
                f'<tr><td class="en" title="{n}">{n}</td>'
                f'<td class="ec">{r["uniq"]}</td>'
                f'<td class="ec">{r["contacts"]}</td>'
                f'<td style="text-align:right;width:90px"><a href="/admin/event-report?name={qn}" '
                f'target="_blank" style="color:#2563eb;font-size:.76rem;font-weight:600">Rapport →</a></td></tr>')
    return f'''
<div class="card">
  <div class="card-h">📊 Statistiques par événement · 30 jours</div>
  <p style="font-size:.78rem;color:#64748b;margin:0 0 .7rem">
    Consultations = 1 par visiteur et par fiche (signal d'intérêt réel, défilement passif exclu).
  </p>
  <div style="overflow-x:auto;-webkit-overflow-scrolling:touch">
  <table class="ev-tbl">
    <thead><tr><th>Événement</th><th style="text-align:right">Consultations (uniques)</th>
    <th style="text-align:right">Clics contact</th><th></th></tr></thead>
    <tbody>{body}</tbody>
  </table>
  </div>
</div>'''


def _render_event_report(name: str) -> str:  # noqa: PLR0912,PLR0915
    """Rapport imprimable une page pour UN événement (offre visibilité) :
    totaux depuis la publication, courbe jour par jour, comparaison zone/type."""
    stats = {"views": 0, "uniq": 0, "contacts": 0}
    try:
        rows = _stats_query(
            "SELECT count(*) FILTER (WHERE type = 'event_read'), "
            "count(DISTINCT visitor_hash) FILTER (WHERE type = 'event_read' AND visitor_hash <> ''), "
            "count(*) FILTER (WHERE type = ANY(%s)) "
            "FROM interactions WHERE event_name = %s", (list(_CONTACT_TYPES), name))
        if rows:
            stats = {"views": rows[0][0], "uniq": rows[0][1], "contacts": rows[0][2]}
    except Exception as exc:
        log.error("Stats : rapport événement impossible : %s", exc)

    today = datetime.datetime.now(
        datetime.timezone(datetime.timedelta(hours=4))).date()

    # Métadonnées : date de publication + date limite (repli : 1re vue en base)
    pub = dl = None
    try:
        r = _stats_query(
            "SELECT published_on, deadline_on FROM event_meta WHERE name = %s", (name,))
        if r:
            pub, dl = r[0]
    except Exception as exc:
        log.error("event_meta : lecture rapport impossible : %s", exc)
    if not pub:
        try:
            r = _stats_query(
                "SELECT min((ts AT TIME ZONE 'Indian/Reunion')::date) FROM interactions "
                "WHERE event_name = %s AND type IN ('event_view', 'event_read')", (name,))
            pub = r[0][0] if r and r[0][0] else None
        except Exception:
            pub = None
    if dl and pub and dl < pub:
        dl = None
    curve_end = min(dl, today) if dl else today
    days_online = max(1, (curve_end - pub).days + 1) if pub else None

    # Zone / type depuis events.json
    zone = ev_type = ""
    peer_names: list = []
    try:
        with open(os.path.join("data", "events.json"), encoding="utf-8") as f:
            events = json.load(f)
        me = next((e for e in events if e.get("name") == name), None)
        if me:
            zone, ev_type = me.get("zone", ""), me.get("type", "")
            peer_names = [e["name"] for e in events
                          if e.get("name") and e["name"] != name
                          and e.get("zone") == zone and e.get("type") == ev_type]
    except Exception as exc:
        log.error("Rapport : lecture events.json impossible : %s", exc)

    # Courbe jour par jour entre publication et limite (ou aujourd'hui).
    # On compte les visiteurs distincts par jour pour rester cohérent avec
    # l'indicateur dédupliqué affiché dans les KPI.
    daily: dict = {}
    if pub:
        try:
            daily = dict(_stats_query(
                "SELECT (ts AT TIME ZONE 'Indian/Reunion')::date AS d, "
                "count(DISTINCT visitor_hash) "
                "FROM interactions WHERE event_name = %s AND type = 'event_read' "
                "AND visitor_hash <> '' "
                "AND (ts AT TIME ZONE 'Indian/Reunion')::date BETWEEN %s AND %s "
                "GROUP BY d", (name, pub, curve_end)))
        except Exception as exc:
            log.error("Rapport : courbe quotidienne impossible : %s", exc)

    # Comparaison : autres événements même zone + même type, ayant des vues.
    # Fenêtres homogènes : publication → min(limite, aujourd'hui) pour chacun.
    # Toutes les comparaisons utilisent COUNT(DISTINCT visitor_hash) pour être
    # cohérentes avec l'indicateur dédupliqué affiché.
    comparison = ""
    if peer_names and stats["uniq"] > 0 and pub:
        try:
            peer_views = dict(_stats_query(
                "SELECT event_name, count(DISTINCT visitor_hash) FROM interactions "
                "WHERE type = 'event_read' AND visitor_hash <> '' "
                "AND event_name = ANY(%s) "
                "GROUP BY event_name", (peer_names,)))
            windowed = _stats_query(
                "SELECT i.event_name, count(DISTINCT i.visitor_hash), "
                "max(m.published_on), max(m.deadline_on) "
                "FROM interactions i JOIN event_meta m ON m.name = i.event_name "
                "WHERE i.type = 'event_read' AND i.visitor_hash <> '' "
                "AND i.event_name = ANY(%s) "
                "AND (m.deadline_on IS NULL OR m.deadline_on >= m.published_on) "
                "AND (i.ts AT TIME ZONE 'Indian/Reunion')::date "
                "BETWEEN m.published_on AND LEAST(COALESCE(m.deadline_on, %s), %s) "
                "GROUP BY i.event_name", (peer_names, today, today))
            active = {p: v for p, v in peer_views.items() if v > 0}
            rates = []
            for _p, v, p_pub, p_dl in windowed:
                p_end = min(p_dl, today) if p_dl else today
                if v > 0:
                    rates.append(v / max(1, (p_end - p_pub).days + 1))
            zt = f"« {html.escape(ev_type)} » de la zone {html.escape(zone)}"
            win_views = sum(daily.values())
            if len(rates) >= 3 and days_online and win_views > 0:
                avg = sum(rates) / len(rates)
                if avg > 0:
                    mult = (win_views / days_online) / avg
                    mult_txt = f"{mult:.1f}".replace(".", ",")
                    comparison = (f"Cette fiche a été consultée <b>{mult_txt}×</b> "
                                  f"{'plus' if mult >= 1 else 'moins'} que la moyenne des "
                                  f"{len(rates)} autres événements {zt} "
                                  f"(en consultations par jour de présence sur le radar).")
            elif len(active) >= 3:
                avg = sum(active.values()) / len(active)
                if avg > 0:
                    mult = stats["uniq"] / avg
                    mult_txt = f"{mult:.1f}".replace(".", ",")
                    comparison = (f"Cette fiche totalise <b>{mult_txt}×</b> "
                                  f"{'plus' if mult >= 1 else 'moins'} de consultations que la "
                                  f"moyenne des {len(active)} autres événements {zt}.")
        except Exception as exc:
            log.error("Rapport : comparaison zone/type impossible : %s", exc)

    n = html.escape(name)
    def _fr(d): return d.strftime("%d/%m/%Y")

    # Graphique SVG (barres par jour ; regroupement hebdo au-delà de 100 jours)
    chart = ('<div style="color:#8a8474;font-size:13px">Aucune consultation '
             'enregistrée sur la période.</div>')
    if pub and daily and sum(daily.values()) > 0:
        span = [pub + datetime.timedelta(days=i) for i in range((curve_end - pub).days + 1)]
        if len(span) > 100:
            buckets: dict = {}
            for d in span:
                wk = d - datetime.timedelta(days=d.weekday())
                buckets[wk] = buckets.get(wk, 0) + daily.get(d, 0)
            keys = sorted(buckets)
            vals = [buckets[k] for k in keys]
            labels = [f"sem. du {k.strftime('%d/%m')}" for k in keys]
            unit = "sem."
        else:
            vals = [daily.get(d, 0) for d in span]
            labels = [d.strftime("%d/%m") for d in span]
            unit = "jour"
        W, H, PAD = 584, 110, 14
        mx = max(vals)
        step = (W - 2 * PAD) / len(vals)
        bw = max(1.5, step - 2)
        bars = "".join(
            f'<rect x="{PAD + i * step:.1f}" y="{H - 15 - (v / mx * (H - 32)):.1f}" '
            f'width="{bw:.1f}" height="{(v / mx * (H - 32)):.1f}" rx="1.5" '
            f'fill="#0e6b52"><title>{labels[i]} : {v}</title></rect>'
            for i, v in enumerate(vals) if v > 0)
        chart = (f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" '
                 f'aria-label="Consultations de la fiche par {unit}">'
                 f'<line x1="{PAD}" y1="{H-15}" x2="{W-PAD}" y2="{H-15}" '
                 f'stroke="#e7e1d2" stroke-width="1"/>{bars}'
                 f'<text x="{PAD}" y="{H-2}" font-size="10" fill="#8a8474">{labels[0]}</text>'
                 f'<text x="{W-PAD}" y="{H-2}" font-size="10" fill="#8a8474" '
                 f'text-anchor="end">{labels[-1]}</text>'
                 f'<text x="{W-PAD}" y="12" font-size="10" fill="#8a8474" '
                 f'text-anchor="end">max : {mx} / {unit}</text></svg>')

    if pub:
        dl_txt = f" · limite de candidature : {_fr(dl)}" if dl else ""
        period = (f"Fiche publiée sur le radar le {_fr(pub)}{dl_txt} · "
                  f"{days_online} jour{'s' if days_online > 1 else ''} d'exposition"
                  f" (au {_fr(curve_end)})")
    else:
        period = f"Totaux mesurés à ce jour ({_fr(today)})"
    # Honnêteté de la mesure : les consultations ne sont comptées que depuis
    # le durcissement du tracking (intérêt réel : interaction ou lecture
    # prolongée, dédupliqué par visiteur). L'ancien comptage « fiche affichée
    # à l'écran » n'est PAS présenté dans ce rapport.
    try:
        r = _stats_query("SELECT min((ts AT TIME ZONE 'Indian/Reunion')::date) "
                         "FROM interactions WHERE type = 'event_read'")
        read_since = r[0][0] if r and r[0][0] else None
    except Exception:
        read_since = None
    period += (" · consultations mesurées depuis le "
               f"{_fr(read_since)} (signal d'intérêt réel, dédupliqué par visiteur)"
               if read_since else
               " · consultations : nouvelle mesure d'intérêt réel (aucune donnée encore)")
    comparison_html = (f'<div class="note" style="border-left:4px solid #0e6b52;'
                       f'margin-top:14px">📈 {comparison}</div>') if comparison else ""
    chart_html = (f'<div class="note" style="margin-top:14px"><b>Consultations '
                  f'{"jour par jour" if pub else ""} :</b><div style="margin-top:10px">'
                  f'{chart}</div></div>') if pub else ""
    return f'''<!DOCTYPE html><html lang="fr"><head><meta charset="utf-8">
<title>Rapport de visibilité — {n}</title>
<style>
  body{{font-family:Georgia,'Times New Roman',serif;background:#f6f4ee;color:#211f1a;
       max-width:640px;margin:0 auto;padding:48px 28px}}
  @media print{{body{{background:#fff}} .noprint{{display:none}}}}
  .head{{text-align:center;margin-bottom:30px}}
  .head img{{width:64px;height:64px;border-radius:50%}}
  .brand{{font-weight:700;font-size:20px;margin-top:10px}}
  .sub{{color:#8a8474;font-size:13px;margin-top:4px}}
  h1{{font-size:22px;margin:26px 0 4px;color:#0e6b52}}
  .period{{color:#8a8474;font-size:13px;margin-bottom:24px}}
  .grid{{display:flex;gap:14px;margin:22px 0}}
  .kpi{{flex:1;background:#fff;border:1px solid #e7e1d2;border-top:4px solid #0e6b52;
       border-radius:12px;padding:18px;text-align:center}}
  .kpi.or{{border-top-color:#a9812f}}
  .val{{font-size:30px;font-weight:700}}
  .lbl{{color:#8a8474;font-size:12px;margin-top:6px}}
  .note{{background:#fff;border:1px solid #e7e1d2;border-radius:12px;padding:16px 18px;
        font-size:13px;line-height:1.6;color:#211f1a;font-family:Arial,sans-serif}}
  footer{{margin-top:30px;text-align:center;color:#8a8474;font-size:12px;
         border-top:1px solid #e7e1d2;padding-top:14px}}
  .noprint{{text-align:center;margin-top:22px}}
  .noprint button{{background:#0e6b52;color:#fff;border:none;border-radius:8px;
                  padding:10px 22px;font-size:14px;cursor:pointer}}
</style></head><body>
<div class="head">
  <img src="/assets/logo_radar_marches.png" alt="Radar des Marchés">
  <div class="brand">Radar des Marchés</div>
  <div class="sub">Rapport de visibilité — radar.artisanspei.re</div>
</div>
<h1>{n}</h1>
<div class="period">{html.escape(period)}{f" · {html.escape(zone)} · {html.escape(ev_type)}" if zone else ""}</div>
<div class="grid">
  <div class="kpi"><div class="val">{stats["uniq"]}</div><div class="lbl">Consultations (par visiteur unique)</div></div>
  <div class="kpi or"><div class="val">{stats["contacts"]}</div><div class="lbl">Clics vers le contact</div></div>
</div>
{chart_html}
{comparison_html}
<div class="note" style="margin-top:14px"><b>Comment lire ces chiffres :</b> les « consultations » comptent
<b>un seul passage par visiteur et par fiche</b> — le simple défilement sans
interaction est exclu (clic ou lecture de ≥ 10 s nécessaire) ; les
« clics vers le contact » comptent les artisans ayant cliqué pour contacter
l'organisateur. Mesure anonyme, sans cookie publicitaire ni traceur tiers.</div>
<footer>Radar des Marchés de La Réunion · radar.artisanspei.re · rapport généré le {_fr(today)}</footer>
<div class="noprint"><button onclick="window.print()">Imprimer / Enregistrer en PDF</button></div>
</body></html>'''


def _render_stats_page(dev_mode: bool, user_name: str, flash: str = "") -> str:  # noqa: PLR0912,PLR0915
    traffic        = _load_traffic_stats()
    q_stats        = _load_questions_stats()
    themes         = _load_themes()
    clicks         = _load_clicks_stats()
    try:
        wa_rows = _stats_query(
            "SELECT day, count, entered_at FROM wa_subscribers ORDER BY day DESC LIMIT 8")
    except Exception as exc:
        log.error("Stats : lecture abonnés WhatsApp impossible : %s", exc)
        wa_rows = []
    # Suggestion pré-remplie : dernier chiffre CONFIRMÉ + clics « inscription
    # WhatsApp » depuis cette confirmation. Ce n'est qu'une aide à la saisie :
    # seule la valeur confirmée par le propriétaire est enregistrée (tous les
    # clics ne deviennent pas membres, certains cliquent deux fois).
    wa_clicks_since = 0
    if wa_rows:
        try:
            # Ancienne ligne sans horodatage (entered_at NULL) : repli sur le
            # début du jour de confirmation, heure Réunion.
            r = _stats_query(
                "SELECT count(*) FROM interactions "
                "WHERE type = 'signup_whatsapp' AND ts > COALESCE(%s, "
                "%s::date::timestamp AT TIME ZONE 'Indian/Reunion')",
                (wa_rows[0][2], wa_rows[0][0]))
            wa_clicks_since = r[0][0] if r else 0
        except Exception as exc:
            log.error("Stats : clics WhatsApp depuis confirmation impossibles : %s", exc)
    wa_suggest = (wa_rows[0][1] + wa_clicks_since) if wa_rows else ""
    # Rappel discret si la dernière confirmation date de plus de 2 jours.
    wa_reminder = ""
    if wa_rows:
        try:
            today_re = _stats_query(
                "SELECT (now() AT TIME ZONE 'Indian/Reunion')::date")[0][0]
            wa_age = (today_re - wa_rows[0][0]).days
            if wa_age > 2:
                wa_reminder = (f'<div style="font-size:.8rem;color:#b45309;margin-top:6px">'
                               f'⏰ Dernière confirmation il y a {wa_age} jours — '
                               f'pensez à mettre le chiffre à jour.</div>')
        except Exception:
            pass
    proposals_html = (_render_field_updates_section(dev_mode)
                      + _render_proposals_section(dev_mode))
    event_stats_html = _render_event_stats_section()
    org_subs_html  = _render_org_submissions_section()
    published_html = _render_published_events_section()
    now_str        = datetime.datetime.now().strftime("%d/%m/%Y à %H:%M")

    # Flash message HTML
    flash_html = ""
    if flash.startswith("ok:"):
        flash_html = f'<div class="flash flash-ok">{flash[3:]}</div>'
    elif flash.startswith("err:"):
        flash_html = f'<div class="flash flash-err">{flash[4:]}</div>'

    # ── Chart: traffic 14 days oldest→newest, skip leading zeros ──────────────
    days14    = list(reversed(traffic["days"][:14]))
    first_nz  = next((i for i, d in enumerate(days14) if d["v"] > 0), len(days14) - 1)
    chart_d   = days14[max(0, first_nz - 1):]
    chart_lbs = json.dumps([d["label"] for d in chart_d])
    chart_v   = json.dumps([d["v"]     for d in chart_d])
    chart_u   = json.dumps([d["u"]     for d in chart_d])

    # ── Referrer sources ───────────────────────────────────────────────────────
    refs       = traffic.get("refs", {})
    ref_order  = ["direct", "google", "facebook", "instagram", "whatsapp",
                  "linkedin", "email", "interne", "autre"]
    ref_labels = {"direct":"Lien direct","google":"Recherche","facebook":"Facebook",
                  "instagram":"Instagram","whatsapp":"WhatsApp","linkedin":"LinkedIn",
                  "email":"Email","interne":"Navigation interne","autre":"Autre"}
    ref_colors = {"direct":"#6366f1","google":"#f59e0b","facebook":"#3b82f6",
                  "instagram":"#ec4899","whatsapp":"#22c55e","linkedin":"#0a66c2",
                  "email":"#0ea5e9","interne":"#cbd5e1","autre":"#94a3b8"}
    refs_data  = [(ref_labels[k], refs.get(k, 0), ref_colors[k])
                  for k in ref_order if refs.get(k, 0) > 0]
    has_refs   = bool(refs_data)
    refs_lj    = json.dumps([r[0] for r in refs_data])
    refs_vj    = json.dumps([r[1] for r in refs_data])
    refs_cj    = json.dumps([r[2] for r in refs_data])

    # ── Referrer legend HTML ───────────────────────────────────────────────────
    ref_legend = ""
    if has_refs:
        ref_legend = '<div class="ref-leg">'
        for lbl, cnt, col in refs_data:
            ref_legend += (
                f'<div class="ref-row"><span class="ref-dot" style="background:{col}"></span>'
                f'<span class="ref-lbl">{lbl}</span><span class="ref-cnt">{cnt}</span></div>'
            )
        ref_legend += '</div>'
    refs_canvas = '<canvas id="refChart"></canvas>' if has_refs else \
                  '<div class="no-chart">Aucune source enregistrée pour le moment</div>'

    # ── Questions récentes du chatbot (texte + date uniquement, anonyme) ───────
    _tz_run = datetime.timezone(datetime.timedelta(hours=4))  # La Réunion
    recent_q = _load_recent_questions(100)
    if recent_q:
        q_rows = "".join(
            f'<div class="qz-row"><span class="qz-ts">'
            f'{datetime.datetime.fromtimestamp(ts, _tz_run).strftime("%d/%m/%Y %H:%M")}</span>'
            f'<span class="qz-txt">{html.escape(q)}</span></div>'
            for ts, q in recent_q
        )
        questions_html = (
            f'<details class="qz-box"><summary class="qz-sum">'
            f'📜 Lire les questions posées — {len(recent_q)} dernière'
            f'{"s" if len(recent_q) > 1 else ""}</summary>'
            f'<div class="qz-list">{q_rows}</div>'
            f'<p class="hint-xs">Texte et date uniquement — aucune donnée '
            f'permettant d\'identifier le visiteur.</p></details>'
        )
    else:
        questions_html = ""

    # ── Themes ─────────────────────────────────────────────────────────────────
    theme_list = (themes or {}).get("themes", [])
    max_cnt    = max((t.get("count", 0) for t in theme_list), default=1) or 1
    if theme_list:
        gen_at = (themes or {}).get("generated_at", "")
        try:    gen_date = datetime.datetime.fromisoformat(gen_at).strftime("%d/%m/%Y")
        except: gen_date = gen_at[:10] if gen_at else "—"  # noqa: E722
        themes_body = (
            f'<div class="th-meta">Analyse du {gen_date} &nbsp;·&nbsp; '
            f'{(themes or {}).get("total_analyzed","?")} questions &nbsp;·&nbsp; 30 derniers jours</div>'
            '<div class="th-list">'
        )
        for t in sorted(theme_list, key=lambda x: x.get("count", 0), reverse=True):
            cnt  = t.get("count", 0)
            pct  = round(cnt / max_cnt * 100)
            themes_body += (
                f'<div class="ti"><div class="ti-row">'
                f'<span class="ti-name">{t.get("name","?")}</span>'
                f'<span class="ti-badge">{cnt} q.</span></div>'
                f'<div class="ti-bar"><div class="ti-fill" style="width:{pct}%"></div></div>'
                f'<div class="ti-ex">{t.get("example","")}</div></div>'
            )
        themes_body += (
            f'</div><p class="hint-xs">Prochaine analyse dans ~'
            f'{round(_THEMES_INTERVAL / 3600 / 24)} jours.</p>'
        )
    else:
        themes_body = (
            '<div class="empty-st"><span>📭</span>'
            '<p>Pas encore d\'analyse disponible.</p>'
            '<span class="empty-sub">L\'analyse se déclenche dès 3 questions, puis toutes les 7 jours.</span>'
            '</div>'
            '<form method="POST" action="/admin/run-analysis" style="margin-top:.8rem;text-align:center">'
            '<button type="submit" style="background:#2563eb;color:#fff;border:none;padding:.45rem 1.1rem;'
            'border-radius:6px;font-size:.85rem;cursor:pointer">⚡ Lancer l\'analyse maintenant</button>'
            '</form>'
        )

    # ── Top events ─────────────────────────────────────────────────────────────
    top_ev_rows = ""
    for i, (name, cnt) in enumerate((clicks.get("top_events") or []), 1):
        top_ev_rows += (
            f'<tr><td class="rk">#{i}</td>'
            f'<td class="en">{html.escape(name[:55])}</td>'
            f'<td class="ec">{cnt}</td></tr>'
        )
    if not top_ev_rows:
        top_ev_rows = '<tr><td colspan="3" class="nd">Aucune donnée pour le moment</td></tr>'

    # ── Dev/auth helpers ───────────────────────────────────────────────────────
    dev_banner  = ('<div class="dev-banner">⚠️ Mode développement — données du workspace, '
                   'pas de la production.</div>') if dev_mode else ""
    badge_html  = f'<span class="badge">🔐 {user_name}</span>' if (not dev_mode and user_name) else ""
    logout_html = '<a href="/admin/logout" class="logout-btn">Déconnexion</a>' if not dev_mode else ""

    # ── Chart JS vars (built as plain f-strings to avoid brace-escaping in the big return) ──
    chart_vars = f"var lbs={chart_lbs},vs={chart_v},us={chart_u};"
    refs_vars  = f"var rl={refs_lj},rv={refs_vj},rc={refs_cj};" if has_refs else ""
    refs_init  = (
        "var rctx=document.getElementById('refChart');"
        "if(rctx){new Chart(rctx,{type:'doughnut',"
        "data:{labels:rl,datasets:[{data:rv,backgroundColor:rc,borderWidth:2,"
        "borderColor:'#fff',hoverOffset:5}]},"
        "options:{responsive:true,maintainAspectRatio:false,cutout:'65%',"
        "plugins:{legend:{display:false},"
        "tooltip:{callbacks:{label:function(c){"
        "return' '+c.label+' : '+c.parsed+' visite'+(c.parsed>1?'s':'');}"
        "}}}}});}"
    ) if has_refs else ""

    return f"""<!doctype html>
<html lang="fr"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Dashboard · Agenda Artisans Réunion</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Inter',system-ui,sans-serif;background:#f1f5f9;color:#0f172a;font-size:14px;min-height:100vh}}
a{{text-decoration:none;color:inherit}}
/* ── Dev banner ── */
.dev-banner{{background:#fef3c7;border-bottom:2px solid #fcd34d;padding:.5rem 1.5rem;font-size:.8rem;color:#92400e;font-weight:500}}
/* ── Header ── */
.hdr{{background:linear-gradient(135deg,#0f172a 0%,#1e3a5f 100%);color:#fff;padding:1.1rem 2rem;display:flex;align-items:center;justify-content:space-between;gap:1rem}}
.hdr-l{{display:flex;flex-direction:column;gap:.15rem}}
.hdr-title{{font-size:1rem;font-weight:700;letter-spacing:-.01em}}
.hdr-sub{{font-size:.72rem;color:#94a3b8}}
.hdr-r{{display:flex;align-items:center;gap:.6rem}}
.badge{{background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.2);color:#e2e8f0;font-size:.73rem;font-weight:500;padding:.3rem .65rem;border-radius:6px}}
.logout-btn{{background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.18);color:#94a3b8;font-size:.73rem;padding:.3rem .65rem;border-radius:6px;cursor:pointer;transition:.15s}}
.logout-btn:hover{{background:rgba(255,255,255,.18);color:#fff}}
/* ── Layout ── */
main{{max-width:1060px;margin:1.75rem auto;padding:0 1.25rem;display:flex;flex-direction:column;gap:1.1rem}}
/* ── KPI strip ── */
.kpi-strip{{display:grid;grid-template-columns:repeat(5,1fr);gap:.85rem}}
.kpi{{background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:1rem 1.1rem;box-shadow:0 1px 3px rgba(0,0,0,.05)}}
.kpi-val{{font-size:1.75rem;font-weight:700;line-height:1;letter-spacing:-.03em}}
.kpi-lbl{{font-size:.68rem;color:#64748b;margin-top:.3rem;font-weight:500;text-transform:uppercase;letter-spacing:.05em}}
.c-blue{{color:#2563eb}} .c-green{{color:#059669}} .c-purple{{color:#7c3aed}}
/* ── Cards ── */
.card{{background:#fff;border:1px solid #e2e8f0;border-radius:14px;padding:1.4rem 1.5rem;box-shadow:0 1px 3px rgba(0,0,0,.05),0 4px 16px rgba(0,0,0,.03)}}
.card-h{{font-size:.7rem;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:#64748b;margin-bottom:1.1rem;display:flex;align-items:center;gap:.45rem}}
/* ── Two-col grid ── */
.g2{{display:grid;grid-template-columns:1fr 1fr;gap:1.1rem}}
/* ── Charts ── */
.chart-wrap{{position:relative;height:195px;width:100%}}
/* ── Ref legend ── */
.ref-leg{{display:flex;flex-direction:column;gap:.4rem;margin-top:.75rem}}
.ref-row{{display:flex;align-items:center;gap:.55rem}}
.ref-dot{{width:9px;height:9px;border-radius:50%;flex-shrink:0}}
.ref-lbl{{font-size:.8rem;color:#374151;flex:1}}
.ref-cnt{{font-size:.8rem;font-weight:600;color:#0f172a}}
.no-chart{{display:flex;align-items:center;justify-content:center;height:195px;color:#94a3b8;font-size:.82rem;font-style:italic}}
/* ── Interactions ── */
.int-row{{display:grid;grid-template-columns:repeat(3,1fr);gap:.85rem;margin-bottom:1.1rem}}
.int-kpi{{background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:.85rem 1rem;text-align:center}}
.int-val{{font-size:1.5rem;font-weight:700}}
.int-lbl{{font-size:.68rem;color:#64748b;margin-top:.2rem;font-weight:500;text-transform:uppercase;letter-spacing:.04em}}
/* ── Table ── */
.ev-tbl{{width:100%;border-collapse:collapse;font-size:.82rem}}
.ev-tbl th{{text-align:left;padding:.45rem .6rem;border-bottom:2px solid #e2e8f0;color:#64748b;font-size:.68rem;font-weight:600;text-transform:uppercase;letter-spacing:.05em}}
.ev-tbl td{{padding:.4rem .6rem;border-bottom:1px solid #f1f5f9;color:#374151}}
.ev-tbl tr:last-child td{{border-bottom:none}}
.ev-tbl tr:hover td{{background:#f8fafc}}
td.rk{{color:#94a3b8;font-weight:600;width:34px;font-size:.73rem}}
td.en{{max-width:400px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
td.ec{{text-align:right;font-weight:700;color:#0f172a;width:55px}}
td.nd{{text-align:center;color:#94a3b8;font-style:italic;padding:1.2rem;font-size:.82rem}}
/* ── Chatbot layout ── */
.cb-split{{display:grid;grid-template-columns:155px 1fr;gap:1.4rem;align-items:start}}
.cb-kpis{{display:flex;flex-direction:column;gap:.75rem}}
.qz-box{{margin-top:1rem;border-top:1px solid #f1f5f9;padding-top:.75rem}}
.qz-sum{{cursor:pointer;font-size:.85rem;font-weight:600;color:#475569}}
.qz-list{{max-height:320px;overflow-y:auto;margin-top:.6rem}}
.qz-row{{display:flex;gap:.6rem;padding:.4rem .2rem;border-bottom:1px solid #f8fafc;align-items:baseline}}
.qz-ts{{font-size:.72rem;color:#94a3b8;white-space:nowrap;flex-shrink:0}}
.qz-txt{{font-size:.83rem;color:#0f172a;line-height:1.4;word-break:break-word}}
/* ── Themes ── */
.th-meta{{font-size:.75rem;color:#64748b;margin-bottom:.9rem;font-style:italic}}
.th-list{{display:flex;flex-direction:column;gap:.65rem}}
.ti{{background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:.7rem .9rem}}
.ti-row{{display:flex;align-items:center;justify-content:space-between;margin-bottom:.3rem}}
.ti-name{{font-weight:600;font-size:.875rem;color:#0f172a}}
.ti-badge{{font-size:.68rem;font-weight:600;color:#2563eb;background:#eff6ff;padding:.15rem .45rem;border-radius:20px}}
.ti-bar{{height:4px;background:#e2e8f0;border-radius:4px;overflow:hidden;margin-bottom:.3rem}}
.ti-fill{{height:100%;background:linear-gradient(90deg,#2563eb,#7c3aed);border-radius:4px}}
.ti-ex{{font-size:.73rem;color:#64748b;font-style:italic}}
/* ── Empty state ── */
.empty-st{{display:flex;flex-direction:column;align-items:center;padding:1.5rem;text-align:center;color:#64748b;gap:.35rem}}
.empty-st span:first-child{{font-size:1.75rem}}
.empty-st p{{font-size:.85rem;font-weight:500;color:#374151}}
.empty-sub{{font-size:.77rem}}
/* ── Flash ── */
.flash{{border-radius:10px;padding:.7rem 1rem;font-size:.83rem;font-weight:500;margin-bottom:.2rem}}
.flash-ok{{background:#dcfce7;color:#15803d;border:1px solid #bbf7d0}}
.flash-err{{background:#fee2e2;color:#b91c1c;border:1px solid #fecaca}}
/* ── Propositions ── */
.prop-summary{{display:flex;flex-wrap:wrap;gap:.4rem .55rem;align-items:center;margin-bottom:1rem}}
.prop-cnt-pub{{background:#dcfce7;color:#15803d;padding:.2rem .65rem;border-radius:20px;font-size:.75rem;font-weight:700}}
.prop-cnt-chk{{background:#fef3c7;color:#b45309;padding:.2rem .65rem;border-radius:20px;font-size:.75rem;font-weight:700}}
.prop-from{{font-weight:400;color:#94a3b8;margin-left:.35rem;font-size:.68rem;text-transform:none;letter-spacing:0}}
.prop-list{{display:flex;flex-direction:column;gap:.7rem}}
.prop-card{{border:1px solid #e2e8f0;border-radius:10px;padding:.85rem 1rem;background:#f8fafc;transition:border-color .15s}}
.prop-card:hover{{border-color:#cbd5e1}}
.prop-top{{display:flex;align-items:flex-start;gap:.55rem;margin-bottom:.4rem;flex-wrap:wrap}}
.prop-name{{font-weight:600;font-size:.88rem;color:#0f172a;flex:1;min-width:0;line-height:1.3}}
.conf-badge{{font-size:.67rem;font-weight:700;padding:.18rem .5rem;border-radius:20px;white-space:nowrap;flex-shrink:0;margin-top:.1rem}}
.conf-vert{{background:#dcfce7;color:#15803d}}
.conf-amb{{background:#fef3c7;color:#b45309}}
.conf-grey{{background:#f1f5f9;color:#64748b}}
.prop-meta{{display:flex;flex-wrap:wrap;gap:.3rem .75rem;margin-bottom:.4rem}}
.prop-meta-item{{font-size:.74rem;color:#475569}}
.prop-notes{{font-size:.74rem;color:#64748b;font-style:italic;margin-bottom:.45rem;background:#f1f5f9;border-radius:6px;padding:.35rem .6rem;line-height:1.45}}
.prop-foot{{display:flex;align-items:center;justify-content:space-between;gap:.6rem;flex-wrap:wrap;margin-top:.5rem;padding-top:.5rem;border-top:1px solid #e2e8f0}}
.prop-src{{font-size:.74rem;color:#2563eb;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:300px;flex:1;min-width:0}}
.prop-src:hover{{text-decoration:underline}}
.prop-actions{{display:flex;gap:.4rem;flex-shrink:0}}
.prop-form{{display:inline}}
.btn-prop{{border:none;border-radius:6px;padding:.32rem .75rem;font-size:.76rem;font-weight:600;cursor:pointer;transition:background .15s;white-space:nowrap}}
.btn-pub{{background:#2563eb;color:#fff}}.btn-pub:hover{{background:#1d4ed8}}
.btn-rej{{background:#f1f5f9;color:#64748b;border:1px solid #e2e8f0}}.btn-rej:hover{{background:#e2e8f0;color:#374151}}
.prop-complete{{display:flex;gap:.4rem;margin:.45rem 0;flex-wrap:wrap}}
.inp-comp{{flex:1;min-width:200px;border:1px solid #e2e8f0;border-radius:6px;padding:.32rem .6rem;font-size:.76rem;color:#0f172a}}
.inp-comp:focus{{outline:none;border-color:#2563eb}}
.btn-comp{{background:#f0f9ff;color:#0369a1;border:1px solid #bae6fd}}.btn-comp:hover{{background:#e0f2fe}}
.prop-report{{font-size:.74rem;color:#92400e;background:#fffbeb;border:1px solid #fde68a;border-radius:6px;padding:.4rem .6rem;margin:.45rem 0;line-height:1.45}}
.prop-running{{font-size:.74rem;color:#0369a1;background:#f0f9ff;border:1px solid #bae6fd;border-radius:6px;padding:.4rem .6rem;margin:.45rem 0}}
/* ── Misc ── */
.hint-xs{{font-size:.72rem;color:#94a3b8;margin-top:.9rem}}
footer{{text-align:center;font-size:.7rem;color:#94a3b8;padding:2rem 0 1.5rem}}
@media(max-width:768px){{
  .kpi-strip{{grid-template-columns:repeat(2,1fr)}}
  .g2,.int-row,.cb-split{{grid-template-columns:1fr}}
  .prop-foot{{flex-direction:column;align-items:flex-start}}
  .prop-src{{max-width:100%;white-space:normal;word-break:break-all}}
  .prop-actions{{width:100%;display:flex;gap:.4rem}}
  .btn-prop{{flex:1;text-align:center;padding:.45rem .5rem}}
}}
</style></head>
<body>
{dev_banner}{flash_html}
<header class="hdr">
  <div class="hdr-l">
    <div class="hdr-title">📊 Dashboard — Agenda Artisans Réunion</div>
    <div class="hdr-sub">Généré le {now_str}</div>
  </div>
  <div class="hdr-r">{badge_html}{logout_html}</div>
</header>

<main>

<div class="kpi-strip">
  <div class="kpi"><div class="kpi-val c-blue">{traffic["last7_v"]}</div><div class="kpi-lbl">Visites · 7 jours</div></div>
  <div class="kpi"><div class="kpi-val">{traffic["last7_u"]}</div><div class="kpi-lbl">Uniques · 7 jours</div></div>
  <div class="kpi"><div class="kpi-val c-blue">{traffic["last30_v"]}</div><div class="kpi-lbl">Visites · 30 jours</div></div>
  <div class="kpi"><div class="kpi-val">{traffic["last30_u"]}</div><div class="kpi-lbl">Uniques · 30 jours</div></div>
  <div class="kpi"><div class="kpi-val c-green">{traffic["total_v"]}</div><div class="kpi-lbl">Total historique</div></div>
</div>

<div class="g2">
  <div class="card">
    <div class="card-h">📈 Trafic — 14 derniers jours</div>
    <div class="chart-wrap"><canvas id="trafficChart"></canvas></div>
    <p class="hint-xs" style="margin-top:.6rem">Barres = visites totales · Ligne = visiteurs uniques (IP hachée)</p>
  </div>
  <div class="card">
    <div class="card-h">🔍 Sources de trafic · 30 j.</div>
    <div class="chart-wrap">{refs_canvas}</div>
    {ref_legend}
  </div>
</div>

<div class="card">
  <div class="card-h">🖱️ Interactions · 30 jours</div>
  <div class="int-row">
    <div class="int-kpi"><div class="int-val c-purple">{clicks["chatbot_open"]}</div><div class="int-lbl">Ouvertures chatbot</div></div>
    <div class="int-kpi"><div class="int-val c-blue">{clicks["event_read"]}</div><div class="int-lbl">Fiches consultées (intérêt réel)</div></div>
    <div class="int-kpi"><div class="int-val c-green">{clicks["contacts"]}</div><div class="int-lbl">Clics contact</div></div>
    <div class="int-kpi"><div class="int-val c-green">{clicks["signup_whatsapp"]}</div><div class="int-lbl">Inscriptions WhatsApp</div></div>
    <div class="int-kpi"><div class="int-val c-blue">{clicks["signup_email"]}</div><div class="int-lbl">Inscriptions email</div></div>
  </div>
  <div class="card-h" style="margin-bottom:.75rem">🏆 Événements les plus consultés</div>
  <table class="ev-tbl">
    <thead><tr><th></th><th>Événement</th><th style="text-align:right">Vues</th></tr></thead>
    <tbody>{top_ev_rows}</tbody>
  </table>
</div>

<div class="card" style="border:1.5px dashed #f59e0b;background:#fffbeb">
  <div class="card-h">📱 Abonnés du groupe WhatsApp
    <span style="font-size:.72rem;font-weight:600;background:#fef3c7;color:#92400e;
    border:1px solid #fcd34d;border-radius:99px;padding:2px 10px;margin-left:8px;vertical-align:middle">
    ✍️ Saisie manuelle — pas un chiffre mesuré automatiquement</span></div>
  <div style="display:flex;gap:1.4rem;flex-wrap:wrap;align-items:flex-start">
    <div>
      <div class="int-val" style="color:#b45309">{wa_rows[0][1] if wa_rows else "—"}</div>
      <div class="int-lbl">{"au " + wa_rows[0][0].strftime("%d/%m/%Y") if wa_rows else "Aucune valeur saisie"}</div>
    </div>
    <div>
      <form method="POST" action="/admin/wa-subscribers" style="display:flex;gap:8px;align-items:center">
        <input type="number" name="count" min="0" max="1000000" required
          placeholder="Nombre d'abonnés" style="width:160px;padding:.45rem .6rem;border:1px solid #fcd34d;
          border-radius:8px;font-size:.9rem" value="{wa_suggest}">
        <button type="submit" style="background:#b45309;color:#fff;border:none;padding:.5rem 1rem;
          border-radius:8px;font-size:.85rem;cursor:pointer">Confirmer (aujourd'hui)</button>
      </form>
      <div style="font-size:.78rem;color:#92400e;margin-top:6px">
        {f"💡 Suggestion à confirmer : {wa_rows[0][1]} (dernier chiffre confirmé) + {wa_clicks_since} clic{'s' if wa_clicks_since > 1 else ''} d'inscription depuis — vérifiez le nombre réel dans WhatsApp avant de confirmer (tous les clics ne deviennent pas membres)." if wa_rows else "Saisissez le nombre réel de membres du groupe."}
      </div>
      {wa_reminder}
    </div>
    <div style="font-size:.8rem;color:#92400e;line-height:1.5">
      Historique : {" · ".join(f"{d.strftime('%d/%m')} : {c}" for d, c, *_ in wa_rows) if wa_rows else "—"}<br>
      À confirmer chaque jour depuis le nombre réel de membres du groupe — seule
      la valeur confirmée est enregistrée, jamais la suggestion seule.
    </div>
  </div>
</div>

{event_stats_html}

{proposals_html}
{org_subs_html}
{published_html}

<div class="card">
  <div class="card-h">💬 Assistant « Le ti artisan futé »</div>
  <div class="cb-split">
    <div class="cb-kpis">
      <div class="kpi"><div class="kpi-val c-purple">{q_stats["last30"]}</div><div class="kpi-lbl">Questions · 30 j.</div></div>
      <div class="kpi"><div class="kpi-val">{q_stats["total"]}</div><div class="kpi-lbl">Total historique</div></div>
    </div>
    <div>
      <div class="card-h" style="margin-bottom:.75rem">🏷️ Thèmes récurrents</div>
      {themes_body}
    </div>
  </div>
  {questions_html}
</div>

</main>
<footer>Dashboard privé · Agenda des Exposants — Artisans de La Réunion</footer>

<script>
{chart_vars}
{refs_vars}
(function(){{
  Chart.defaults.font.family="'Inter',system-ui,sans-serif";
  Chart.defaults.font.size=12;
  var ctx=document.getElementById('trafficChart');
  if(ctx){{
    new Chart(ctx,{{
      data:{{labels:lbs,datasets:[
        {{type:'bar',label:'Visites',data:vs,backgroundColor:'rgba(37,99,235,.1)',
          borderColor:'rgba(37,99,235,.55)',borderWidth:1.5,borderRadius:4,order:2}},
        {{type:'line',label:'Uniques',data:us,borderColor:'#059669',
          backgroundColor:'rgba(5,150,105,.07)',borderWidth:2,pointRadius:3,
          pointBackgroundColor:'#059669',tension:.35,fill:true,order:1}}
      ]}},
      options:{{
        responsive:true,maintainAspectRatio:false,
        interaction:{{mode:'index',intersect:false}},
        plugins:{{
          legend:{{position:'top',labels:{{boxWidth:10,padding:14,usePointStyle:true}}}},
          tooltip:{{callbacks:{{label:function(c){{return' '+c.dataset.label+' : '+c.parsed.y;}}}}}}
        }},
        scales:{{
          x:{{grid:{{display:false}},ticks:{{maxRotation:0}}}},
          y:{{beginAtZero:true,grid:{{color:'rgba(0,0,0,.04)'}},ticks:{{precision:0}}}}
        }}
      }}
    }});
  }}
  {refs_init}
}})();
</script>
</body></html>"""


class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # Masquer les requêtes GET/HEAD habituelles pour garder les logs lisibles
        if args and str(args[1]) not in ("200", "304"):
            log.info(fmt, *args)

    def _redirect_to_canonical(self) -> bool:
        """301 vers le domaine canonique si la requête arrive sur un autre
        domaine public (ex. radar.fhservices.re). Le chemin et la query string
        sont conservés. Les hôtes de développement (localhost, IP, *.replit.*)
        ne sont jamais redirigés."""
        host = (self.headers.get("Host") or "").strip().lower()
        if host.startswith("["):                    # IPv6 littéral, ex. [::1]:5000
            return False
        host = host.split(":")[0]
        if (not host or host == _CANONICAL_HOST
                or host == "localhost"
                or host.endswith((".replit.dev", ".repl.co", ".replit.app"))
                or all(c.isdigit() or c == "." for c in host)):   # adresse IPv4
            return False
        self.send_response(301)
        self.send_header("Location", f"https://{_CANONICAL_HOST}{self.path}")
        self.send_header("Content-Length", "0")
        self.end_headers()
        return True

    def do_GET(self):
        if self._redirect_to_canonical():
            return
        if self.path == "/health":
            git_ok = _git_available()
            status = {
                "status": "ok" if git_ok else "degraded",
                "git_available": git_ok,
                "sync_available": git_ok,
            }
            body = json.dumps(status).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.split("?")[0] in ("/organisateurs", "/organisateurs/"):
            self._handle_org_page()
        elif self.path == "/admin/contacts.csv":
            self._handle_contacts_csv()
        elif self.path.split("?")[0] in ("/admin", "/admin/"):
            self._handle_admin()
        elif self.path.split("?")[0] == "/admin/event-report":
            self._handle_event_report()
        elif self.path == "/admin/logout":
            self.send_response(302)
            self.send_header("Location", "/admin")
            self.send_header(
                "Set-Cookie",
                f"{_SESSION_COOKIE}=; Path=/admin; HttpOnly; Secure; "
                "SameSite=Strict; Max-Age=0"
            )
            self.end_headers()
        elif self.path.split("?")[0] == "/robots.txt":
            body = (
                "User-agent: *\n"
                "Allow: /\n"
                "Disallow: /admin\n"
                "Disallow: /sync\n"
                "Disallow: /track\n"
                "Disallow: /chat\n"
                "\n"
                f"Sitemap: https://{_CANONICAL_HOST}/sitemap.xml\n"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.split("?")[0] == "/sitemap.xml":
            # Généré côté Mac et committé à la racine du dépôt — servi tel quel.
            try:
                with open("sitemap.xml", "rb") as f:
                    body = f.read()
            except OSError:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/xml; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith(("/data", "/.git", "/scripts")):
            # Fichiers internes (soumissions, contacts privés, dépôt git) —
            # jamais servis publiquement.
            self.send_response(404)
            self.end_headers()
        else:
            # Enregistre les visites du site public (GET classiques uniquement)
            # Visite comptée UNIQUEMENT pour une vraie page (le site n'en a
            # qu'une : « / ») et hors robots — favicon, icônes, assets et
            # sondes automatiques ne sont plus des « visites ».
            page_path = urllib.parse.urlparse(self.path).path
            ua        = self.headers.get("User-Agent", "")
            if page_path in ("/", "/index.html") and not _UA_BOT_RE.search(ua):
                ip       = (self.headers.get("X-Forwarded-For") or self.client_address[0]).split(",")[0].strip()
                referrer = self.headers.get("Referer", "")
                _record_visit(ip, referrer, self.path, ua)
            super().do_GET()

    def _json(self, code: int, data: dict) -> None:
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_chat(self) -> None:
        ip = (self.headers.get("X-Forwarded-For") or self.client_address[0]).split(",")[0].strip()
        if not _check_rate(ip):
            self._json(429, {"error": "Limite atteinte. Maximum 20 messages par heure."})
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, ValueError):
            self._json(400, {"error": "Requête invalide."})
            return
        user_msg = str(body.get("message", "")).strip()[:1000]
        history = [
            {"role": h["role"], "content": str(h["content"])[:600]}
            for h in body.get("history", [])[-8:]
            if h.get("role") in ("user", "assistant")
        ]
        if not user_msg:
            self._json(400, {"error": "Message vide."})
            return
        if _is_events_q(user_msg):
            system = _SYS_EVENTS.format(events=_load_events())
            tier   = "FAST"
        else:
            system = _SYS_ADMIN
            tier   = "STRONG"
        model = _get_model(tier)
        # Enregistrement anonyme de la question pour les statistiques
        _record_question(user_msg, tier)
        _record_click("chat_question", "", _visitor_hash(ip))
        reply = _claude(model, system, history + [{"role": "user", "content": user_msg}])
        self._json(200, {"reply": reply})

    def _handle_admin(self) -> None:
        """Sert la page de statistiques.

        - Workspace dev (REPLIT_DEPLOYMENT absent) : accès direct avec bannière.
        - Production : vérifie le cookie de session posé par POST /admin/login.
        """
        is_deployed = bool(os.environ.get("REPLIT_DEPLOYMENT"))
        dev_mode    = not is_deployed

        if is_deployed:
            token    = _get_session_cookie(self.headers)
            username = _verify_session_token(token) if token else None

            if not username:
                body = _render_auth_required().encode("utf-8")
                self.send_response(401)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                log.info("Admin : accès refusé (cookie %s).",
                         "absent" if not token else "invalide/expiré")
                return
        else:
            username = "dev"

        # Flash message depuis query string (?pub=ok ou ?err=<message>)
        qs     = urllib.parse.parse_qs(self.path.partition("?")[2])
        flash  = ""
        if "pub" in qs:
            flash = "ok:✅ Événement publié et pushé sur GitHub."
        elif "comp" in qs:
            flash = "ok:🔎 Vérification IA lancée — rechargez la page dans ~1 minute."
        elif "ok" in qs:
            detail = html.escape(urllib.parse.unquote(qs["ok"][0]))
            flash  = f"ok:✅ {detail}"
        elif "err" in qs:
            detail = html.escape(urllib.parse.unquote(qs["err"][0]))
            flash  = f"err:❌ Erreur : {detail}"

        body = _render_stats_page(dev_mode=dev_mode, user_name=username, flash=flash).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, no-cache")
        self.end_headers()
        self.wfile.write(body)
        log.info("Admin : accès accordé (user=%r).", username)

    def _handle_admin_login(self) -> None:
        """Vérifie le mot de passe soumis via POST /admin/login.

        Si correct → pose un cookie de session signé et redirige vers /admin.
        Si incorrect → re-affiche le formulaire avec un message d'erreur.
        """
        length   = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(length).decode("utf-8", errors="replace")
        params   = urllib.parse.parse_qs(raw_body)
        password = params.get("password", [""])[0]

        admin_pw = os.environ.get("ADMIN_PASSWORD", "")
        if not admin_pw:
            log.error("ADMIN_PASSWORD n'est pas défini — connexion impossible.")
            body = _render_auth_required(error="Configuration manquante côté serveur.").encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if not hmac.compare_digest(password, admin_pw):
            log.warning("Admin : tentative de connexion échouée.")
            body = _render_auth_required(error="Mot de passe incorrect.").encode("utf-8")
            self.send_response(401)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        token = _make_session_token("admin")
        log.info("Admin : session créée.")
        self.send_response(302)
        self.send_header("Location", "/admin")
        self.send_header(
            "Set-Cookie",
            f"{_SESSION_COOKIE}={token}; Path=/admin; HttpOnly; Secure; "
            f"SameSite=Strict; Max-Age={_SESSION_TTL}"
        )
        self.end_headers()

    def _redirect_admin(self, qs: str = "") -> None:
        """Redirige vers /admin (avec query string optionnel pour flash)."""
        loc = f"/admin?{qs}" if qs else "/admin"
        self.send_response(302)
        self.send_header("Location", loc)
        self.end_headers()

    def _admin_authorized(self) -> bool:
        """Vrai si dev_mode ou session admin valide (même règle que /admin)."""
        if not os.environ.get("REPLIT_DEPLOYMENT"):
            return True
        token = _get_session_cookie(self.headers)
        return bool(token and _verify_session_token(token))

    def _client_ip(self) -> str:
        return (self.headers.get("X-Forwarded-For")
                or self.client_address[0]).split(",")[0].strip()

    # ── Soumissions organisateurs ────────────────────────────────────────

    def _handle_org_page(self) -> None:
        """GET /organisateurs — page publique du formulaire."""
        qs = urllib.parse.parse_qs(self.path.partition("?")[2])
        flash = "ok" if "ok" in qs else ("rate" if "rate" in qs else "")
        body = _render_organisateurs_page(flash=flash).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _handle_org_submit(self) -> None:
        """POST /organisateurs — enregistre une soumission (file de relecture)."""
        length   = int(self.headers.get("Content-Length", 0))
        if length > 50_000:
            self.send_response(413)
            self.end_headers()
            return
        raw_body = self.rfile.read(length).decode("utf-8", errors="replace")
        params   = {k: v[0].strip() for k, v in urllib.parse.parse_qs(raw_body).items()}

        # Honeypot : rejet silencieux (on affiche le succès pour ne rien révéler)
        if params.get("website"):
            log.info("Soumission organisateur ignorée (honeypot rempli).")
            self._redirect("/organisateurs?ok=1")
            return

        if not _check_org_rate(self._client_ip()):
            self._redirect("/organisateurs?rate=1")
            return

        # Type : liste ou champ libre « Autre »
        type_choice = params.get("type_choice", "")
        type_autre  = params.get("type_autre", "")[:60]
        ev_type = type_autre if (type_choice == "Autre" or type_autre) else type_choice

        fields = {
            "name":  params.get("name", "")[:120],
            "zone":  params.get("zone", ""),
            "type":  ev_type[:60],
            "org":   params.get("org", "")[:120],
            "place": params.get("place", "")[:160],
            "when":  params.get("when", "")[:160],
            "deadline": params.get("deadline", "")[:160],
            "apply": params.get("apply", "")[:600],
            "email": params.get("email", "")[:120],
            "phone": params.get("phone", "")[:40],
            "social": params.get("social", "")[:120],
            "desc":  params.get("desc", "")[:600],
        }
        links = _parse_links(params.get("links", "")[:2000])
        submitter_name  = params.get("submitter_name", "")[:120]
        submitter_phone = params.get("submitter_phone", "")[:40]

        errors = []
        for label, cond in [
            ("le nom de l'événement", fields["name"]),
            ("la zone", fields["zone"] in _ZONES),
            ("le type d'événement", fields["type"]),
            ("l'organisateur", fields["org"]),
            ("le lieu", fields["place"]),
            ("la date ou période", fields["when"]),
            ("au moins un lien valide", links),
            ("comment candidater", fields["apply"]),
            ("un email de contact valide",
             re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", fields["email"] or "")),
            ("la description", fields["desc"]),
            ("votre nom", submitter_name),
            ("votre téléphone", submitter_phone),
        ]:
            if not cond:
                errors.append(label)
        if errors:
            form = dict(params)
            flash = "err:Champs manquants ou invalides : " + ", ".join(errors) + "."
            body = _render_organisateurs_page(flash=flash, form=form).encode("utf-8")
            self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        sub = {
            "id": uuid.uuid4().hex[:12],
            "ts": datetime.datetime.now().isoformat(timespec="seconds"),
            "status": "pending",
            "fields": fields,
            "links": links,
            "submitter_name": submitter_name,
            "submitter_phone": submitter_phone,
            "reviewer_note": "",
        }
        with _submissions_lock:
            subs = _load_json_list(_SUBMISSIONS_FILE)
            # Garde anti double-envoi : même nom + même email déjà soumis
            # dans les 5 dernières minutes → on ignore le doublon (succès
            # silencieux, la 1re soumission est déjà enregistrée).
            now = datetime.datetime.now()
            for prev in subs:
                if (prev.get("fields", {}).get("name", "").strip().lower()
                        == fields["name"].strip().lower()
                        and prev.get("fields", {}).get("email", "").strip().lower()
                        == fields["email"].strip().lower()):
                    try:
                        age = (now - datetime.datetime.fromisoformat(prev["ts"])).total_seconds()
                    except (ValueError, KeyError):
                        continue
                    if age < 300:
                        log.info("Soumission organisateur ignorée (doublon <5 min) : %s",
                                 fields["name"])
                        self._redirect("/organisateurs?ok=1")
                        return
            subs.append(sub)
            _record_click("org_submission", "", _visitor_hash(self._client_ip()))
            _save_json_list(_SUBMISSIONS_FILE, subs)
        log.info("Nouvelle soumission organisateur : %s (%s)",
                 fields["name"], sub["id"])

        # Durabilité + notification immédiate — en arrière-plan (serveur mono-thread)
        def _notify():
            _push_org_files(["data/organizer_submissions.json"])
            text_body = (
                "Une nouvelle proposition d'événement vient d'arriver sur "
                "/organisateurs :\n\n"
                + "\n".join(f"- {k} : {v}" for k, v in fields.items() if v)
                + "\n- liens : " + " ; ".join(links)
                + f"\n- soumis par : {submitter_name} ({submitter_phone})"
                + "\n\nÀ relire dans /admin (section Soumissions organisateurs)."
            )
            e = lambda v: html.escape(str(v))  # noqa: E731
            rows = "".join(
                f'<div style="padding:3px 0"><span style="color:#8a8474">{e(k)}'
                f'&nbsp;:</span> {e(v)}</div>'
                for k, v in fields.items() if v)
            rows += ('<div style="padding:3px 0"><span style="color:#8a8474">'
                     'liens&nbsp;:</span> ' + " ; ".join(
                         f'<a href="{e(u)}" style="color:#0e6b52">{e(u)}</a>'
                         for u in links) + '</div>')
            html_body = _email_html(
                "Nouvelle soumission d'un organisateur",
                _email_card("warn", f"📨 {e(fields['name'])}", rows)
                + _email_card(
                    "ok", "Soumis par",
                    f'{e(submitter_name)} ({e(submitter_phone)})<br>'
                    f'À relire dans <a href="{_SITE_URL}/admin" '
                    f'style="color:#0e6b52">/admin</a> — section '
                    'Soumissions organisateurs.'))
            _send_email(
                f"📨 Nouvelle soumission organisateur : {fields['name']}",
                text_body, _ORG_OWNER_EMAIL, html_body=html_body,
            )
        threading.Thread(target=_notify, daemon=True).start()
        self._redirect("/organisateurs?ok=1")

    def _redirect(self, loc: str) -> None:
        self.send_response(303)
        self.send_header("Location", loc)
        self.end_headers()

    def _find_submission(self, sub_id: str, subs: list):
        return next((s for s in subs
                     if s.get("id") == sub_id and s.get("status") == "pending"), None)

    def _handle_org_approve(self) -> None:
        """POST /admin/org-approve — publie via le MÊME chemin que les candidats IA."""
        if not self._admin_authorized():
            self.send_response(403)
            self.end_headers()
            return
        length   = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(length).decode("utf-8", errors="replace")
        params   = urllib.parse.parse_qs(raw_body)
        sub_id   = params.get("id", [""])[0]
        note     = params.get("note", [""])[0][:300]

        with _submissions_lock:
            subs = _load_json_list(_SUBMISSIONS_FILE)
            sub  = self._find_submission(sub_id, subs)
        if not sub:
            self._redirect_admin(
                "err=" + urllib.parse.quote("Soumission introuvable ou déjà traitée.", safe=""))
            return

        ok, msg = _publish_event_to_repo(_submission_to_event(sub))
        if not ok:
            log.error("Publication soumission %s échouée : %s", sub_id, msg)
            self._redirect_admin("err=" + urllib.parse.quote(msg, safe=""))
            return

        with _submissions_lock:
            subs = _load_json_list(_SUBMISSIONS_FILE)
            sub  = next((s for s in subs if s.get("id") == sub_id), sub)
            sub["status"] = "approved"
            sub["reviewer_note"] = note
            _save_json_list(_SUBMISSIONS_FILE, subs)
            paths, _ = _update_org_contact(sub)

        ev_name = sub["fields"].get("name", "")
        org_email = sub["fields"].get("email", "")

        def _post_approve():
            _push_org_files(paths)
            if org_email:
                text_body = (
                    "Bonjour,\n\n"
                    f"Bonne nouvelle : votre événement « {ev_name} » a été validé "
                    "et figure désormais sur le Radar des marchés de La Réunion :\n"
                    f"{_SITE_URL}\n\n"
                    "Merci d'avoir pris le temps de nous le proposer — n'hésitez "
                    "pas à soumettre vos prochains événements sur "
                    f"{_SITE_URL}/organisateurs\n\n"
                    "Bien cordialement,\nRadar Marchés Réunion"
                )
                ev = html.escape(ev_name)
                html_body = _email_html(
                    "Votre événement est en ligne",
                    _email_card(
                        "ok", f"✅ « {ev} » est publié",
                        "Bonjour,<br><br>Bonne nouvelle : votre événement a été "
                        "validé et figure désormais sur le Radar des marchés de "
                        f'La Réunion : <a href="{_SITE_URL}" '
                        f'style="color:#0e6b52">{_SITE_URL.replace("https://", "")}</a>.')
                    + _email_card(
                        "warn", "Et la suite ?",
                        "Merci d'avoir pris le temps de nous le proposer — "
                        "n'hésitez pas à soumettre vos prochains événements sur "
                        f'<a href="{_SITE_URL}/organisateurs" '
                        f'style="color:#0e6b52">la page organisateurs</a>.<br><br>'
                        "Bien cordialement,<br>Radar Marchés Réunion"))
                _send_email(
                    f"✅ Votre événement « {ev_name} » est en ligne",
                    text_body, org_email, html_body=html_body,
                )
        threading.Thread(target=_post_approve, daemon=True).start()
        self._redirect_admin("pub=ok")

    def _handle_org_reject(self) -> None:
        """POST /admin/org-reject — marque la soumission rejetée (avec note)."""
        if not self._admin_authorized():
            self.send_response(403)
            self.end_headers()
            return
        length   = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(length).decode("utf-8", errors="replace")
        params   = urllib.parse.parse_qs(raw_body)
        sub_id   = params.get("id", [""])[0]
        note     = params.get("note", [""])[0][:300]

        with _submissions_lock:
            subs = _load_json_list(_SUBMISSIONS_FILE)
            sub  = self._find_submission(sub_id, subs)
            if sub:
                sub["status"] = "rejected"
                sub["reviewer_note"] = note
                _save_json_list(_SUBMISSIONS_FILE, subs)
        if sub:
            threading.Thread(
                target=_push_org_files,
                args=(["data/organizer_submissions.json"],), daemon=True).start()
        self._redirect_admin()

    def _handle_org_delete(self) -> None:
        """POST /admin/org-delete — supprime définitivement une soumission."""
        if not self._admin_authorized():
            self.send_response(403)
            self.end_headers()
            return
        length   = int(self.headers.get("Content-Length", 0))
        params   = urllib.parse.parse_qs(
            self.rfile.read(length).decode("utf-8", errors="replace"))
        sub_id   = params.get("id", [""])[0]

        removed = False
        with _submissions_lock:
            subs = _load_json_list(_SUBMISSIONS_FILE)
            kept = [s for s in subs if s.get("id") != sub_id]
            if len(kept) != len(subs):
                _save_json_list(_SUBMISSIONS_FILE, kept)
                removed = True
        if removed:
            log.info("Soumission organisateur supprimée définitivement : %s", sub_id)
            threading.Thread(
                target=_push_org_files,
                args=(["data/organizer_submissions.json"],), daemon=True).start()
        self._redirect_admin()

    def _handle_delete_proposal(self) -> None:
        """POST /admin/delete-proposal — supprime définitivement un candidat IA
        (retiré du fichier de veille + décisions + complétions)."""
        if not self._admin_authorized():
            self.send_response(403)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0))
        params = urllib.parse.parse_qs(
            self.rfile.read(length).decode("utf-8", errors="replace"))
        key    = params.get("key", [""])[0]
        if not key:
            self._redirect_admin()
            return

        filename, candidates = _load_latest_proposal()
        pushes = []
        if filename and candidates:
            # Ne supprime qu'UN candidat (les clés peuvent entrer en collision
            # si deux candidats partagent la même source).
            kept, removed_one = [], False
            for c in candidates:
                if not removed_one and _candidate_key(c) == key:
                    removed_one = True
                    continue
                kept.append(c)
            if removed_one:
                path = os.path.join(_PENDING_DIR, filename)
                try:
                    with open(path, encoding="utf-8") as f:
                        data = json.load(f)
                    if "new_events_candidates" in data:
                        data["new_events_candidates"] = kept
                    else:
                        data["candidates"] = kept
                    with open(path, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
                    pushes.append(path)
                except Exception as exc:
                    log.error("Suppression candidat impossible : %s", exc)
                    self._redirect_admin("err=" + urllib.parse.quote(
                        f"Suppression impossible : {exc}", safe=""))
                    return

        # Effacer aussi toute décision / complétion associée (aucune trace).
        with _decisions_lock:
            try:
                with open(_DECISIONS_FILE, encoding="utf-8") as f:
                    dec = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                dec = {}
            if key in dec:
                del dec[key]
                with open(_DECISIONS_FILE, "w", encoding="utf-8") as f:
                    json.dump(dec, f, ensure_ascii=False, indent=2)
                pushes.append("data/pending_decisions.json")
        with _completions_lock:
            try:
                with open(_COMPLETIONS_FILE, encoding="utf-8") as f:
                    comp = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                comp = {}
            if key in comp:
                del comp[key]
                with open(_COMPLETIONS_FILE, "w", encoding="utf-8") as f:
                    json.dump(comp, f, ensure_ascii=False, indent=2)
                pushes.append("data/pending_completions.json")

        if pushes:
            log.info("Candidat supprimé définitivement : %s", key)
            threading.Thread(
                target=_push_runtime_file,
                args=(pushes, "Suppression définitive d'une proposition",
                      "suppression"), daemon=True).start()
        self._redirect_admin()

    def _handle_event_remove(self) -> None:
        """POST /admin/event-remove — retire un événement publié du site."""
        if not self._admin_authorized():
            self.send_response(403)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0))
        params = urllib.parse.parse_qs(
            self.rfile.read(length).decode("utf-8", errors="replace"))
        name   = params.get("name", [""])[0].strip()
        if not name:
            self._redirect_admin()
            return
        ok, msg = _remove_event_from_repo(name)
        if ok:
            self._redirect_admin("ok=" + urllib.parse.quote(msg, safe=""))
        else:
            log.error("Retrait échoué : %s", msg)
            self._redirect_admin("err=" + urllib.parse.quote(msg, safe=""))

    def _handle_event_update(self) -> None:
        """POST /admin/event-update — corrige la fiche d'un événement publié."""
        if not self._admin_authorized():
            self.send_response(403)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0))
        params = urllib.parse.parse_qs(
            self.rfile.read(length).decode("utf-8", errors="replace"))
        get = lambda k: params.get(k, [""])[0].strip()  # noqa: E731
        orig_name = get("orig_name")
        event = {k: get(k) for k in _EVENT_FIELDS}
        try:
            event["month"] = int(event["month"])
        except ValueError:
            event["month"] = 99
        if not (1 <= event["month"] <= 12):
            event["month"] = 99
        if not orig_name or not event["name"]:
            self._redirect_admin("err=" + urllib.parse.quote(
                "Nom manquant — correction ignorée.", safe=""))
            return
        ok, msg = _update_event_in_repo(orig_name, event)
        if ok:
            self._redirect_admin("ok=" + urllib.parse.quote(msg, safe=""))
        else:
            log.error("Correction échouée : %s", msg)
            self._redirect_admin("err=" + urllib.parse.quote(msg, safe=""))

    def _handle_event_report(self) -> None:
        """GET /admin/event-report?name=X — rapport imprimable (accès admin)."""
        if not self._admin_authorized():
            self.send_response(403)
            self.end_headers()
            return
        qs   = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        name = qs.get("name", [""])[0].strip()
        if not name:
            self.send_response(404)
            self.end_headers()
            return
        body = _render_event_report(name).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_contacts_csv(self) -> None:
        """GET /admin/contacts.csv — export privé de l'annuaire de contacts."""
        if not self._admin_authorized():
            self.send_response(403)
            self.end_headers()
            return
        import csv
        import io
        contacts = _load_json_list(_ORG_CONTACTS_FILE)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["email", "nom", "telephone", "reseaux_sociaux",
                    "premier_contact", "dernier_contact",
                    "evenements_soumis", "notes_internes"])
        for c in contacts:
            w.writerow([c.get("email", ""), c.get("name", ""), c.get("phone", ""),
                        c.get("social", ""), c.get("first_contact", ""),
                        c.get("last_contact", ""), c.get("events_submitted", 0),
                        c.get("notes", "")])
        body = buf.getvalue().encode("utf-8-sig")
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition",
                         'attachment; filename="contacts_organisateurs.csv"')
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _handle_apply_update(self) -> None:
        """POST /admin/apply-update — applique une correction de fiche existante.

        Ne crée jamais rien : si la fiche a disparu entre la proposition et le clic,
        on refuse plutôt que d'inventer. `name` et `zone` sont la clé d'identité et
        restent intouchables ici — les modifier serait un renommage.
        """
        dev_mode = not os.environ.get("REPLIT_DEPLOYMENT")
        if not dev_mode:
            token    = _get_session_cookie(self.headers)
            username = _verify_session_token(token) if token else None
            if not username:
                self.send_response(403); self.end_headers(); return

        length   = int(self.headers.get("Content-Length", 0))
        params   = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8", errors="replace"))
        key      = params.get("key", [""])[0]
        if not key:
            self.send_response(400); self.end_headers(); return

        err = lambda m: "err=" + urllib.parse.quote(m, safe="")

        upd = next((u for u in _load_field_updates() if _field_update_key(u) == key), None)
        if upd is None:
            log.warning("apply-update : correction %s introuvable", key)
            self._redirect_admin(err(
                "Correction introuvable — le fichier de veille a changé depuis "
                "l'affichage de la page. Rechargez /admin.")); return

        # ⚠️ _git_ops_lock est un threading.Lock() NON réentrant, et
        # _push_decisions() le reprend en interne. L'appeler depuis l'intérieur du
        # bloc verrouillé bloquait le fil pour toujours : la correction partait
        # bien sur GitHub, puis la requête ne revenait jamais — d'où un « rien ne
        # se passe » alors que la donnée avait changé. Tout ce qui touche à git
        # reste ici ; les décisions se règlent APRÈS libération, comme le fait
        # _handle_publish.
        resultat = {"etat": None, "msg": "", "nom": upd.get("event_name", ""), "touches": []}
        with _git_ops_lock:
            ok, msg = _git_pull_for_publish()
            if not ok:
                resultat.update(etat="err", msg=f"Synchronisation git impossible : {msg}")
            else:
                try:
                    with open(os.path.join("data", "events.json"), encoding="utf-8") as f:
                        events = json.load(f)
                except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
                    resultat.update(etat="err", msg=f"events.json illisible : {exc}")
                    events = None
                if events is not None:
                    nom = resultat["nom"]
                    cible = next((e for e in events if e.get("name") == nom), None)
                    if cible is None:
                        resultat.update(etat="err", msg=(
                            f"Fiche « {nom} » introuvable dans events.json — "
                            f"ce canal ne crée jamais de fiche."))
                    else:
                        for champ, valeur in (upd.get("changes") or {}).items():
                            if champ in ("name", "zone") or champ not in _EVENT_FIELDS:
                                log.warning("apply-update : champ « %s » refusé sur « %s »",
                                            champ, nom)
                                continue
                            if cible.get(champ) != valeur:
                                cible[champ] = valeur
                                resultat["touches"].append(champ)
                        if not resultat["touches"]:
                            resultat.update(etat="deja", msg=(
                                f"« {nom} » était déjà à jour : aucun champ à modifier. "
                                f"La correction est retirée de la liste."))
                        else:
                            ok, msg = _write_events_rebuild_and_push(
                                events, f"Corriger : {nom} ({', '.join(resultat['touches'])})")
                            log.info("apply-update « %s » : %s — %s",
                                     nom, ", ".join(resultat["touches"]), msg)
                            resultat.update(etat="ok" if ok else "err",
                                            msg=msg if ok else f"Écriture impossible : {msg}")

        # ---- hors verrou : enregistrement de la décision et réponse ----
        if resultat["etat"] in ("ok", "deja"):
            _save_decision(key, "published")
            _push_decisions()
        if resultat["etat"] == "ok":
            self._redirect_admin("pub=ok")
        else:
            self._redirect_admin(err(resultat["msg"] or "Échec inconnu."))

    def _handle_publish(self) -> None:
        """POST /admin/publish — publie un candidat Vérifié dans events.json."""
        dev_mode = not os.environ.get("REPLIT_DEPLOYMENT")
        if not dev_mode:
            token    = _get_session_cookie(self.headers)
            username = _verify_session_token(token) if token else None
            if not username:
                self.send_response(403)
                self.end_headers()
                return

        length   = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(length).decode("utf-8", errors="replace")
        params   = urllib.parse.parse_qs(raw_body)
        key      = params.get("key", [""])[0]

        if not key:
            self.send_response(400)
            self.end_headers()
            return

        _, candidates = _load_latest_proposal()
        candidate = next((c for c in candidates if _candidate_key(c) == key), None)
        if candidate:
            # La complétion IA (si validée) prime toujours sur la fiche brute :
            # c'est elle qui est affichée dans l'admin, y compris pour une
            # fiche « Vérifié » corrigée puis re-vérifiée.
            comp = _load_completions().get(key)
            if comp and comp.get("status") == "done" and comp.get("event"):
                candidate["event"] = comp["event"]
                log.info("Publication : fiche issue de la complétion IA (%s).", key)
        if not candidate or not candidate.get("event"):
            self._redirect_admin("err=Candidat+introuvable+ou+sans+fiche+complète.")
            return

        ok, msg = _publish_event_to_repo(candidate["event"])
        if ok:
            _save_decision(key, "published")
            _push_decisions()
            self._redirect_admin("pub=ok")
        else:
            log.error("Publication échouée : %s", msg)
            self._redirect_admin("err=" + urllib.parse.quote(msg, safe=""))

    def _handle_reject(self) -> None:
        """POST /admin/reject — rejette un candidat (le masque de la file)."""
        dev_mode = not os.environ.get("REPLIT_DEPLOYMENT")
        if not dev_mode:
            token    = _get_session_cookie(self.headers)
            username = _verify_session_token(token) if token else None
            if not username:
                self.send_response(403)
                self.end_headers()
                return

        length   = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(length).decode("utf-8", errors="replace")
        params   = urllib.parse.parse_qs(raw_body)
        key      = params.get("key", [""])[0]

        if key:
            _save_decision(key, "rejected")
            _push_decisions()
        self._redirect_admin()

    def _handle_complete(self) -> None:
        """POST /admin/complete — lance la vérification/complétion IA d'un candidat."""
        dev_mode = not os.environ.get("REPLIT_DEPLOYMENT")
        if not dev_mode:
            token    = _get_session_cookie(self.headers)
            username = _verify_session_token(token) if token else None
            if not username:
                self.send_response(403)
                self.end_headers()
                return

        length   = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(length).decode("utf-8", errors="replace")
        params   = urllib.parse.parse_qs(raw_body)
        key      = params.get("key", [""])[0]
        info     = params.get("info", [""])[0].strip()[:600]

        if not key:
            self.send_response(400)
            self.end_headers()
            return

        _, candidates = _load_latest_proposal()
        candidate = next((c for c in candidates if _candidate_key(c) == key), None)
        if not candidate:
            self._redirect_admin("err=Candidat+introuvable.")
            return

        _save_completion(key, {"status": "running", "report": ""})
        threading.Thread(target=_run_completion_job, args=(key, candidate, info),
                         daemon=True, name="ai-complete").start()
        self._redirect_admin("comp=run")

    def _handle_wa_subscribers(self) -> None:
        """POST /admin/wa-subscribers — compteur d'abonnés WhatsApp (saisie manuelle)."""
        if not self._admin_authorized():
            self.send_response(403)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0))
        raw    = self.rfile.read(min(length, 1000)).decode("utf-8", errors="replace")
        val    = urllib.parse.parse_qs(raw).get("count", [""])[0].strip()
        if not val.isdigit() or int(val) > 1_000_000:
            self._redirect_admin("err=" + urllib.parse.quote("Nombre d'abonnés invalide"))
            return
        try:
            day  = _stats_query("SELECT (now() AT TIME ZONE 'Indian/Reunion')::date")[0][0]
            conn = _stats_connect()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO wa_subscribers (day, count) VALUES (%s, %s) "
                        "ON CONFLICT (day) DO UPDATE SET count = EXCLUDED.count, "
                        "entered_at = now()", (day, int(val)))
            finally:
                conn.close()
            self._redirect_admin("ok=" + urllib.parse.quote("Abonnés WhatsApp enregistrés"))
        except Exception as exc:
            log.error("Abonnés WhatsApp : enregistrement impossible : %s", exc)
            self._redirect_admin("err=" + urllib.parse.quote("Enregistrement impossible (base)"))

    def _handle_run_analysis(self) -> None:
        """Déclenche l'analyse des thèmes manuellement (POST /admin/run-analysis)."""
        dev_mode = not os.environ.get("REPLIT_DEPLOYMENT")
        if not dev_mode:
            token    = _get_session_cookie(self.headers)
            username = _verify_session_token(token) if token else None
            if not username:
                self.send_response(403)
                self.end_headers()
                return
        threading.Thread(target=_run_theme_analysis, daemon=True, name="theme-manual").start()
        self.send_response(302)
        self.send_header("Location", "/admin")
        self.end_headers()

    def _handle_track(self) -> None:
        """Reçoit un ping de tracking côté client (fire-and-forget)."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            body   = self.rfile.read(min(length, 512))
            entry  = json.loads(body)
            ev     = entry.get("e", "")[:32]
            name   = entry.get("n", "")[:80]
            if ev in ("chatbot_open", "candidater", "event_read",
                      "signup_whatsapp", "signup_email",
                      "contact_email", "contact_phone", "contact_social", "contact_url"):
                ip = (self.headers.get("X-Forwarded-For")
                      or self.client_address[0]).split(",")[0].strip()
                _record_click(ev, name, _visitor_hash(ip))
        except Exception:
            pass  # tracking silencieux — on n'interrompt jamais l'utilisateur
        self.send_response(204)
        self.end_headers()

    def do_POST(self):
        if self.path == "/track":
            self._handle_track()
            return

        if self.path == "/admin/login":
            self._handle_admin_login()
            return

        if self.path == "/admin/run-analysis":
            self._handle_run_analysis()
            return

        if self.path == "/admin/wa-subscribers":
            self._handle_wa_subscribers()
            return

        if self.path == "/admin/publish":
            self._handle_publish()
            return

        if self.path == "/admin/apply-update":
            self._handle_apply_update(); return
        if self.path == "/admin/reject":
            self._handle_reject()
            return

        if self.path == "/admin/complete":
            self._handle_complete()
            return

        if self.path.split("?")[0] in ("/organisateurs", "/organisateurs/"):
            self._handle_org_submit()
            return

        if self.path == "/admin/org-approve":
            self._handle_org_approve()
            return

        if self.path == "/admin/org-reject":
            self._handle_org_reject()
            return

        if self.path == "/admin/org-delete":
            self._handle_org_delete()
            return

        if self.path == "/admin/delete-proposal":
            self._handle_delete_proposal()
            return

        if self.path == "/admin/event-remove":
            self._handle_event_remove()
            return

        if self.path == "/admin/event-update":
            self._handle_event_update()
            return

        if self.path == "/chat":
            self._handle_chat()
            return

        if self.path != "/sync":
            self.send_response(404)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", 0))
        payload = self.rfile.read(length)
        sig = self.headers.get("X-Hub-Signature-256", "")

        if not verify_signature(payload, sig):
            log.warning("Signature webhook invalide — requête rejetée.")
            self.send_response(403)
            self.end_headers()
            self.wfile.write(b"Forbidden")
            return

        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Bad Request")
            return

        ref = data.get("ref", "")
        if ref != f"refs/heads/{BRANCH}":
            log.info("Push sur '%s' ignoré (seul '%s' est surveillé).", ref, BRANCH)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Ignored")
            return

        log.info("Push détecté sur %s — git pull en cours…", BRANCH)
        threading.Thread(target=git_pull, daemon=True).start()

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    # ThreadingHTTPServer : une requête admin lente (SQL) ne bloque plus les
    # visiteurs du site public. L'état partagé est déjà protégé par des locks.
    server = http.server.ThreadingHTTPServer(("0.0.0.0", port), Handler)
    log.info("Serveur démarré sur le port %d", port)
    log.info("Webhook disponible sur POST /sync")

    # Alerte immédiate si git est absent au démarrage
    if not _git_available():
        log.warning(
            "ALERTE MODE DÉGRADÉ : le serveur démarre SANS dépôt git. "
            "GitHub était inaccessible au démarrage (scripts/start.sh n'a pas pu initialiser git). "
            "Le endpoint /sync est indisponible. "
            "Consultez GET /health pour surveiller l'état. "
            "Redémarrez le serveur via scripts/start.sh pour rétablir la synchronisation."
        )
        # Notification unique (email ou webhook) — lancée dans un thread pour ne pas bloquer le démarrage
        threading.Thread(target=_send_degraded_alert, daemon=True, name="degraded-alert").start()
        # Thread daemon : rappels périodiques tant que git reste absent
        threading.Thread(target=_periodic_git_warning, daemon=True, name="git-warn").start()
    else:
        log.info("Dépôt git détecté — endpoint /sync opérationnel.")

    if not WEBHOOK_SECRET:
        log.warning(
            "GITHUB_WEBHOOK_SECRET non défini ! "
            "Définissez ce secret Replit pour sécuriser le webhook."
        )

    # Vérification périodique des modèles Claude (démarrage immédiat + toutes les 24 h)
    threading.Thread(target=_model_check_loop, daemon=True, name="model-check").start()

    # Analyse hebdomadaire des thèmes de questions du chatbot
    threading.Thread(target=_theme_analysis_loop, daemon=True, name="theme-analysis").start()

    # Statistiques persistantes (PostgreSQL) : écriture, rétention 24 mois,
    # reprise unique de l'historique fichiers.
    if psycopg2 and _DB_URL:
        threading.Thread(target=_stats_writer_loop, daemon=True, name="stats-writer").start()
        threading.Thread(target=_stats_retention_loop, daemon=True, name="stats-retention").start()
        threading.Thread(target=_stats_snapshot_loop, daemon=True, name="stats-snapshot").start()
        threading.Thread(target=_import_legacy_stats, daemon=True, name="stats-import").start()
        log.info("Statistiques persistantes : base PostgreSQL active.")
    else:
        log.error("Statistiques persistantes INDISPONIBLES (DATABASE_URL/psycopg2 manquant).")
    log.info("Page de statistiques disponible sur GET /admin (accès restreint au propriétaire Replit).")

    server.serve_forever()
