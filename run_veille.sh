#!/bin/zsh
# run_veille.sh — Lance la veille quotidienne via l'agent Claude (headless).
# Appelé par launchd chaque jour à 4h. Écrit proposition_MAJ_*.md + data/pending/*.
# NE PUBLIE RIEN (le playbook interdit publier.py). Journalise dans veille.log.

set -e
PROJECT_DIR="/Users/fhubert/Claude/radarartisans"
cd "$PROJECT_DIR"

# launchd ne fournit qu'un PATH minimal (/usr/bin:/bin:/usr/sbin:/sbin) où le CLI
# `claude` (installé via npm dans ~/.npm-global/bin) est absent -> "command not
# found" et sortie 127. On l'ajoute explicitement plutôt que de dépendre du
# profil de login, qui n'est pas toujours chargé sous launchd.
export PATH="$HOME/.npm-global/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
CLAUDE_BIN="$(command -v claude || echo "$HOME/.npm-global/bin/claude")"

STAMP=$(date "+%Y-%m-%d %H:%M:%S")
echo "===== VEILLE $STAMP =====" >> veille.log

# Export de la note "Radar Inbox" (captures Instagram/FB via le raccourci iPhone).
# Fait en AppleScript natif (osascript) car l'agent headless n'a PAS accès au
# serveur MCP Apple Notes (celui-ci n'existe que dans les sessions interactives
# enrichies, pas dans le CLI `claude` standard lancé ici). Aucune dépendance MCP.
mkdir -p data/inbox_mobile_archive
NOTE_BODY=$(osascript -e '
tell application "Notes"
  try
    set theNote to first note whose name is "Radar Inbox"
    return body of theNote
  on error
    return ""
  end try
end tell
' 2>>veille.log || true)

if [ -n "$NOTE_BODY" ]; then
  echo "$NOTE_BODY" > data/inbox_mobile_export.txt
  cp data/inbox_mobile_export.txt "data/inbox_mobile_archive/radar_inbox_$(date +%F).txt"
  echo "[note] Radar Inbox exportée -> data/inbox_mobile_export.txt" >> veille.log
else
  rm -f data/inbox_mobile_export.txt
  echo "[note] Radar Inbox vide ou introuvable" >> veille.log
fi

# claude en mode -p (print / non interactif). On autorise uniquement les outils
# nécessaires : recherche, lecture de pages, lecture/écriture de fichiers, et
# python3 pour status_check.py. Tout le reste est refusé automatiquement.
"$CLAUDE_BIN" -p "$(cat veille_agent.md)" \
  --allowedTools WebSearch WebFetch Read Write Edit Glob Grep "Bash(python3:*)" \
  --permission-mode acceptEdits \
  --add-dir "$PROJECT_DIR" \
  >> veille.log 2>&1

# Une fois la veille passée, on vide la note pour repartir propre la semaine
# suivante — le contenu brut reste archivé dans data/inbox_mobile_archive/.
if [ -n "$NOTE_BODY" ]; then
  osascript -e '
  tell application "Notes"
    try
      set theNote to first note whose name is "Radar Inbox"
      set body of theNote to "<div>Radar Inbox</div>"
    end try
  end tell
  ' 2>>veille.log || true
  echo "[note] Radar Inbox vidée après traitement" >> veille.log
fi

RC=$?
echo "----- fin veille (rc=$RC) $(date '+%H:%M:%S') -----" >> veille.log

# Notification macOS de fin.
LATEST=$(ls -t proposition_MAJ_*.md 2>/dev/null | head -1)
osascript -e "display notification \"Proposition prête : ${LATEST:-aucune}\" with title \"Radar Marchés — veille\"" 2>/dev/null || true

exit $RC
