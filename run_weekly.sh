#!/bin/zsh
# run_weekly.sh — Chaîne hebdomadaire "zéro intervention" (Niveau 1) :
#   1) veille (agent Claude) -> proposition + pending
#   2) publier.py --auto -> publie SEULEMENT statuts + nouveaux "Vérifié"
#      institutionnels (plafonnés), pousse sur GitHub, déclenche Replit.
#   Le reste (Probable / privé / social) attend votre validation.
#
# ⚠️ Ne devient réellement "en ligne sans intervention" qu'une fois configurés :
#    - git push non interactif (remote + clé SSH/token),
#    - REPLIT_DEPLOY_HOOK dans .env (sinon le redeploy reste un clic manuel).
# Pour activer le mode hebdo complet, pointez le .plist sur ce script au lieu de
# run_veille.sh.

set -e
PROJECT_DIR="/Users/fhubert/Library/Mobile Documents/com~apple~CloudDocs/PROJETS/CLAUDE/COWORKS/Projects/ARTISANS/radar-marches"
cd "$PROJECT_DIR"

# 1) veille
./run_veille.sh || true

# 2) auto-publication (Niveau 1) sur le dernier pending produit
./venv/bin/python publier.py --auto >> veille.log 2>&1 || true

LATEST=$(ls -t proposition_MAJ_*.md 2>/dev/null | head -1)
osascript -e "display notification \"Cycle hebdo terminé — relisez ${LATEST:-la proposition} pour valider le reste\" with title \"Radar Marchés\"" 2>/dev/null || true
