#!/bin/zsh
# run_veille.sh — Lance la veille hebdomadaire via l'agent Claude (headless).
# Appelé par launchd le lundi. Écrit proposition_MAJ_*.md + data/pending/*.
# NE PUBLIE RIEN (le playbook interdit publier.py). Journalise dans veille.log.

set -e
PROJECT_DIR="/Users/fhubert/Library/Mobile Documents/com~apple~CloudDocs/PROJETS/CLAUDE/COWORKS/Projects/ARTISANS/radar-marches"
cd "$PROJECT_DIR"

STAMP=$(date "+%Y-%m-%d %H:%M:%S")
echo "===== VEILLE $STAMP =====" >> veille.log

# claude en mode -p (print / non interactif). On autorise uniquement les outils
# nécessaires : recherche, lecture de pages, lecture/écriture de fichiers, et
# python3 pour status_check.py. Tout le reste est refusé automatiquement.
claude -p "$(cat veille_agent.md)" \
  --allowedTools WebSearch WebFetch Read Write Edit Glob Grep "Bash(python3:*)" \
  --permission-mode acceptEdits \
  --add-dir "$PROJECT_DIR" \
  >> veille.log 2>&1

RC=$?
echo "----- fin veille (rc=$RC) $(date '+%H:%M:%S') -----" >> veille.log

# Notification macOS de fin.
LATEST=$(ls -t proposition_MAJ_*.md 2>/dev/null | head -1)
osascript -e "display notification \"Proposition prête : ${LATEST:-aucune}\" with title \"Radar Marchés — veille\"" 2>/dev/null || true

exit $RC
