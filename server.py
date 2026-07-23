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

    def do_POST(self):
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
