#!/bin/bash
# Script de démarrage pour le déploiement VM.
#
# ORDRE CRITIQUE : git checkout AVANT python3
#   Le snapshot Replit contient le code au moment du Publish. Si des correctifs
#   ont été poussés sur GitHub entre deux Publish, git checkout les apporte.
#   Python doit démarrer APRÈS pour charger la bonne version du code.
#
# Les healthchecks échouent pendant les ~8 s de git init, mais Replit tolère
# ce délai de démarrage (retry automatique jusqu'à ce que le port réponde).

set -uo pipefail

REPO_URL="https://github.com/FHSERVICES974/radar-marches-reunion.git"
WORK_DIR="$(cd "$(dirname "$0")/.." && pwd)"
MAX_RETRIES=3
RETRY_DELAY=5

cd "$WORK_DIR"

# Désactiver tout credential helper — la prod ne doit jamais tenter replit-git-askpass.
# Les pulls (fetch public) n'en ont pas besoin ; les pushes utilisent GITHUB_TOKEN.
git config --global credential.helper ''
git config --global init.defaultBranch main

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
        echo "[start.sh] Échec tentative $attempt — nettoyage..."
        rm -rf .git
        attempt=$((attempt + 1))
        [ "$attempt" -le "$MAX_RETRIES" ] && sleep "$RETRY_DELAY"
    done
    return 1
}

# ── Étape 1 : initialiser le dépôt git ──────────────────────────────────────
# git checkout -f main écrase server.py avec la version GitHub (correctifs inclus).
# Python doit démarrer APRÈS cette étape pour charger le bon code.
if [ ! -d ".git" ]; then
    echo "[start.sh] Pas de dépôt git — initialisation depuis GitHub..."
    if ! init_git_repo; then
        echo "[start.sh] ⚠️  git init échoué après $MAX_RETRIES tentatives." >&2
        echo "[start.sh] ⚠️  Le serveur démarre avec le snapshot (code potentiellement ancien)." >&2
        echo "[start.sh] ⚠️  /sync indisponible jusqu'au prochain redémarrage réussi." >&2
        rm -rf .git
    fi
else
    echo "[start.sh] Dépôt git existant — pas de réinitialisation."
fi

# ── Étape 2 : démarrer Python avec le code git-checkedout ───────────────────
exec python3 server.py
