#!/bin/zsh
# ingest_docs.sh — Traite les documents déposés dans data/inbox_docs/ via l'agent
# Claude (lecture PDF/scan/docx incluse). Écrit une proposition, ne publie rien.
# Lancez-le après avoir déposé un ou plusieurs documents.

set -e
PROJECT_DIR="/Users/fhubert/Library/Mobile Documents/com~apple~CloudDocs/PROJETS/CLAUDE/COWORKS/Projects/ARTISANS/radar-marches"
cd "$PROJECT_DIR"

# Rien à traiter ? on sort proprement.
COUNT=$(ls -1 data/inbox_docs 2>/dev/null | grep -viE '^(processed|README.txt)$' | wc -l | tr -d ' ')
if [ "$COUNT" = "0" ]; then
  echo "Aucun document dans data/inbox_docs/ — rien à faire."
  osascript -e 'display notification "Aucun document à traiter" with title "Radar Marchés — ingestion"' 2>/dev/null || true
  exit 0
fi

echo "$(date '+%F %T') — ingestion de $COUNT document(s)" >> ingest.log
claude -p "$(cat ingest_agent.md)" \
  --allowedTools WebSearch WebFetch Read Write Edit Glob Grep "Bash(python3:*)" "Bash(ls:*)" "Bash(mv:*)" \
  --permission-mode acceptEdits \
  --add-dir "$PROJECT_DIR" \
  >> ingest.log 2>&1

LATEST=$(ls -t proposition_docs_*.md 2>/dev/null | head -1)
osascript -e "display notification \"Documents traités : ${LATEST:-voir ingest.log}\" with title \"Radar Marchés — ingestion\"" 2>/dev/null || true
echo "Terminé. Proposition : ${LATEST:-(voir ingest.log)}"
