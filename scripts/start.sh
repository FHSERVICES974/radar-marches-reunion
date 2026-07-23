#!/bin/bash
# Script de démarrage pour le déploiement VM.
# En production Replit, le conteneur reçoit un snapshot du code sans dépôt git.
# Ce script initialise le dépôt depuis GitHub avant de lancer le serveur,
# ce qui permet au webhook /sync de faire git pull correctement.

set -euo pipefail

REPO_URL="https://github.com/FHSERVICES974/radar-marches-reunion.git"
WORK_DIR="$(cd "$(dirname "$0")/.." && pwd)"

cd "$WORK_DIR"

if [ ! -d ".git" ]; then
    echo "[start.sh] Pas de dépôt git — initialisation depuis GitHub..."
    git init
    git remote add origin "$REPO_URL"
    git fetch origin main --depth=1
    git checkout -f main
    echo "[start.sh] Dépôt git initialisé avec succès."
else
    echo "[start.sh] Dépôt git existant détecté."
fi

exec python3 server.py
