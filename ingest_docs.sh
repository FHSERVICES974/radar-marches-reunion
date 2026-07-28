#!/bin/zsh
# ingest_docs.sh — Traite les documents déposés dans data/inbox_docs/ via l'agent
# Claude (lecture PDF/scan/docx incluse). Écrit une proposition, ne publie rien.
# Lancez-le après avoir déposé un ou plusieurs documents.

set -e
PROJECT_DIR="/Users/fhubert/Claude/radarartisans"
cd "$PROJECT_DIR"

# Voir run_veille.sh : PATH minimal sous launchd, `claude` (npm) doit être résolu
# explicitement pour éviter un "command not found" (sortie 127).
export PATH="$HOME/.npm-global/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
CLAUDE_BIN="$(command -v claude || echo "$HOME/.npm-global/bin/claude")"

# Passerelle iPhone : le projet est hors iCloud (launchd exige un disque local,
# voir README), donc l'iPhone ne peut plus déposer directement dans
# data/inbox_docs/. Il dépose dans ce dossier iCloud à part, qu'on rapatrie ici
# avant traitement.
ICLOUD_INBOX="$HOME/Library/Mobile Documents/com~apple~CloudDocs/RadarInbox"
if [ -d "$ICLOUD_INBOX" ]; then
  mkdir -p data/inbox_docs
  find "$ICLOUD_INBOX" -maxdepth 1 -type f ! -name '.*' -exec mv {} data/inbox_docs/ \;
fi

# Rien à traiter ? on sort proprement.
COUNT=$(ls -1 data/inbox_docs 2>/dev/null | grep -viE '^(processed|README.txt)$' | wc -l | tr -d ' ')
if [ "$COUNT" = "0" ]; then
  echo "Aucun document dans data/inbox_docs/ — rien à faire."
  osascript -e 'display notification "Aucun document à traiter" with title "Radar Marchés — ingestion"' 2>/dev/null || true
  exit 0
fi

echo "$(date '+%F %T') — ingestion de $COUNT document(s)" >> ingest.log
"$CLAUDE_BIN" -p "$(cat ingest_agent.md)" \
  --allowedTools WebSearch WebFetch Read Write Edit Glob Grep "Bash(python3:*)" "Bash(ls:*)" "Bash(mv:*)" \
  --permission-mode acceptEdits \
  --add-dir "$PROJECT_DIR" \
  >> ingest.log 2>&1

LATEST=$(ls -t proposition_docs_*.md 2>/dev/null | head -1)
osascript -e "display notification \"Documents traités : ${LATEST:-voir ingest.log}\" with title \"Radar Marchés — ingestion\"" 2>/dev/null || true
echo "Terminé. Proposition : ${LATEST:-(voir ingest.log)}"
