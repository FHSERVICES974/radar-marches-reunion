#!/bin/zsh
# ingest_docs.sh — Traite les documents déposés dans data/inbox_docs/ via l'agent
# Claude (lecture PDF/scan/docx incluse). Écrit une proposition, ne publie rien.
# Lancez-le après avoir déposé un ou plusieurs documents.

set -e
# Tourne sur le Mac ET sur le serveur (voir run_veille.sh) : emplacement déduit
# du script, et osascript neutralisé là où il n'existe pas.
PROJECT_DIR="${0:A:h}"
if ! command -v osascript >/dev/null 2>&1; then
  osascript() { return 1; }
fi
cd "$PROJECT_DIR"

# Voir run_veille.sh : PATH minimal sous launchd, `claude` (npm) doit être résolu
# explicitement pour éviter un "command not found" (sortie 127).
export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
CLAUDE_BIN="$(command -v claude || echo "$HOME/.npm-global/bin/claude")"

# .env : le jeton d'authentification du CLI (voir run_veille.sh). Lu, jamais exécuté.
if [ -f .env ]; then
  while IFS= read -r ligne || [ -n "$ligne" ]; do
    case "$ligne" in ''|'#'*) continue ;; esac
    if printf '%s' "$ligne" | grep -qE '^[A-Za-z_][A-Za-z0-9_]*='; then
      cle=${ligne%%=*}; val=${ligne#*=}
      val=${val%\"}; val=${val#\"}; val=${val%\'}; val=${val#\'}
      export "$cle=$val"
    fi
  done < .env
fi

# Passerelle mail : on relève d'abord la boîte, pour traiter aussi ce qui vient
# d'arriver par courriel (« RADAR » dans l'objet).
if [ -f recuperer_mails.py ] && [ -x ./venv/bin/python ]; then
  ./venv/bin/python recuperer_mails.py
fi

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
