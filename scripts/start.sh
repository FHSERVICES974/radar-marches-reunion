#!/bin/bash
# Script de démarrage pour le déploiement VM.
# En production Replit, le conteneur reçoit un snapshot du code sans dépôt git.
#
# ORDRE CRITIQUE :
#   1. Python démarre en arrière-plan immédiatement → healthcheck Replit passe dès
#      la première seconde (sinon Replit déclare le déploiement échoué).
#   2. L'init git se fait pendant que Python tourne → /sync devient disponible
#      quelques secondes après le démarrage, sans bloquer la réponse HTTP.

set -uo pipefail

REPO_URL="https://github.com/FHSERVICES974/radar-marches-reunion.git"
WORK_DIR="$(cd "$(dirname "$0")/.." && pwd)"
MAX_RETRIES=3
RETRY_DELAY=5  # secondes entre chaque tentative

cd "$WORK_DIR"

# ── Étape 1 : démarrer le serveur Python immédiatement ──────────────────────
# Permet au healthcheck Replit de recevoir un HTTP 200 dès le départ.
# Si .git n'existe pas encore, server.py démarre en mode dégradé (sans /sync)
# et se rétablit automatiquement dès que l'étape 2 a terminé l'init git.
python3 server.py &
SERVER_PID=$!

# ── Étape 2 : initialiser le dépôt git (en arrière-plan, pendant que Python tourne) ──
init_git_repo() {
    local attempt=1
    while [ "$attempt" -le "$MAX_RETRIES" ]; do
        echo "[start.sh] Tentative $attempt/$MAX_RETRIES d'initialisation du dépôt git..."
        if git init \
            && git remote add origin "$REPO_URL" 2>/dev/null || true \
            && git fetch origin main --depth=1 \
            && git checkout -f main; then
            echo "[start.sh] Dépôt git initialisé avec succès (tentative $attempt)."
            return 0
        fi
        echo "[start.sh] Échec de la tentative $attempt. Nettoyage avant retry..."
        rm -rf .git
        attempt=$((attempt + 1))
        if [ "$attempt" -le "$MAX_RETRIES" ]; then
            echo "[start.sh] Nouvelle tentative dans $RETRY_DELAY secondes..."
            sleep "$RETRY_DELAY"
        fi
    done
    return 1
}

if [ ! -d ".git" ]; then
    echo "[start.sh] Pas de dépôt git — initialisation depuis GitHub..."
    if ! init_git_repo; then
        echo "[start.sh] ⚠️  ALERTE : Impossible d'initialiser le dépôt git après $MAX_RETRIES tentatives." >&2
        echo "[start.sh] ⚠️  GitHub est peut-être inaccessible. Le serveur tourne avec les fichiers du snapshot (code potentiellement ancien)." >&2
        echo "[start.sh] ⚠️  La fonctionnalité /sync sera indisponible jusqu'à un redémarrage réussi." >&2
        rm -rf .git
    fi
else
    echo "[start.sh] Dépôt git existant détecté."
fi

# ── Étape 3 : attendre la fin du serveur ────────────────────────────────────
# Quand Python s'arrête (crash ou signal), bash se termine → Replit redémarre
# le conteneur automatiquement.
wait "$SERVER_PID"
