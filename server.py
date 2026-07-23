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
import subprocess
import threading
import time

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
