#!/usr/bin/env python3
"""
Serveur statique + webhook GitHub.
Sert index.html sur le port 5000 et expose /sync pour déclencher
un git pull automatique à chaque push sur la branche main.
"""

import hashlib
import hmac
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
import urllib.request
from email.message import EmailMessage

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


def _send_email_alert(recipient: str) -> bool:
    """Envoie une alerte par email via SMTP.
    Retourne True en cas de succès."""
    smtp_host = os.environ.get("SMTP_HOST", "localhost")
    smtp_port = int(os.environ.get("SMTP_PORT", 587))
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_password = os.environ.get("SMTP_PASSWORD", "")
    smtp_from = os.environ.get("SMTP_FROM", "noreply@localhost")

    msg = EmailMessage()
    msg["Subject"] = "⚠️ Serveur démarré en mode dégradé (git absent)"
    msg["From"] = smtp_from
    msg["To"] = recipient
    msg.set_content(_ALERT_MESSAGE)

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as server:
            server.ehlo()
            if smtp_port != 25:
                server.starttls()
                server.ehlo()
            if smtp_user and smtp_password:
                server.login(smtp_user, smtp_password)
            server.send_message(msg)
        log.info("Alerte email envoyée à %s.", recipient)
        return True
    except Exception as exc:
        log.error("Échec envoi alerte email : %s", exc)
        return False


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


def git_pull():
    """Lance git pull origin main dans le répertoire courant.

    En déploiement VM Replit, le dépôt est initialisé par scripts/start.sh
    au démarrage, donc git pull fonctionne normalement.
    """
    import os as _os

    # Vérification préventive : est-on dans un dépôt git ?
    if not _os.path.isdir(".git"):
        log.error(
            "ERREUR CRITIQUE : Pas de dépôt git dans ce conteneur. "
            "Le déploiement doit utiliser scripts/start.sh pour initialiser git. "
            "Vérifiez que la cible de déploiement est 'vm' dans .replit."
        )
        return

    try:
        result = subprocess.run(
            ["git", "pull", "origin", BRANCH],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            log.info("git pull réussi :\n%s", result.stdout.strip())
            _invalidate_events_cache()
        else:
            log.error(
                "git pull a échoué (code %d) :\n%s",
                result.returncode,
                result.stderr.strip(),
            )
    except subprocess.TimeoutExpired:
        log.error("git pull a expiré après 60 secondes.")
    except Exception as exc:
        log.error("Erreur inattendue lors du git pull : %s", exc)


# ── Le ti artisan futé — Assistant IA ─────────────────────────────────────

_ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
_MODEL_FAST   = "claude-haiku-4-5-20251001"   # questions sur les événements du site
_MODEL_STRONG = "claude-sonnet-4-5-20250929"  # questions administratives / recherche

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
    """Lit index.html et retourne un résumé lisible de tous les événements."""
    with _events_lock:
        if _events_cache[0] is not None:
            return _events_cache[0]
        try:
            with open("index.html", encoding="utf-8") as f:
                html = f.read()
            m = re.search(r"const EVENTS = \[(.+?)\];", html, re.DOTALL)
            if not m:
                _events_cache[0] = ""
                return ""
            raw = m.group(1)
            names     = re.findall(r'name:"([^"]+)"',     raw)
            zones     = re.findall(r'zone:"([^"]+)"',     raw)
            types_    = re.findall(r'type:"([^"]+)"',     raw)
            places    = re.findall(r'place:"([^"]+)"',    raw)
            whens     = re.findall(r'when:"([^"]+)"',     raw)
            statuses  = re.findall(r'status:"([^"]+)"',   raw)
            deadlines = re.findall(r'deadline:"([^"]*)"', raw)
            applies   = re.findall(r'apply:"([^"]+)"',    raw)
            contacts  = re.findall(r'contact:"([^"]+)"',  raw)
            _STATUS_LABELS = {
                "open": "Candidature ouverte",
                "soon": "À surveiller / appel à venir",
                "closed": "Clôturée",
                "perm": "Marché permanent",
            }
            lines = []
            for i, name in enumerate(names):
                row = [f"▸ {name}"]
                if i < len(zones):    row.append(f"  Zone : {zones[i]}")
                if i < len(types_):   row.append(f"  Type : {types_[i]}")
                if i < len(places):   row.append(f"  Lieu : {places[i]}")
                if i < len(whens):    row.append(f"  Quand : {whens[i]}")
                if i < len(statuses):
                    row.append(f"  Statut : {_STATUS_LABELS.get(statuses[i], statuses[i])}")
                if i < len(deadlines) and deadlines[i]:
                    row.append(f"  Délai candidature : {deadlines[i]}")
                if i < len(applies):  row.append(f"  Comment candidater : {applies[i]}")
                if i < len(contacts): row.append(f"  Contact : {contacts[i]}")
                lines.append("\n".join(row))
            _events_cache[0] = "\n\n".join(lines)
            log.info("Cache événements chargé (%d événements).", len(names))
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


class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # Masquer les requêtes GET/HEAD habituelles pour garder les logs lisibles
        if args and str(args[1]) not in ("200", "304"):
            log.info(fmt, *args)

    def do_GET(self):
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
        else:
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
            model  = _MODEL_FAST
        else:
            system = _SYS_ADMIN
            model  = _MODEL_STRONG
        reply = _claude(model, system, history + [{"role": "user", "content": user_msg}])
        self._json(200, {"reply": reply})

    def do_POST(self):
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
    server = http.server.HTTPServer(("0.0.0.0", port), Handler)
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
    server.serve_forever()
