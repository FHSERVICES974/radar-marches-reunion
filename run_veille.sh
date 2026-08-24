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

# Se resynchroniser AVANT de travailler : la page /admin sur Replit peut avoir
# publié des événements depuis la dernière veille (elle écrit events.json et
# pousse sur GitHub). Sans ce pull, le Mac travaillerait sur une base périmée et
# la veille proposerait des doublons.
git pull --rebase --autostash origin main >> veille.log 2>&1 \
  && echo "[git] resynchronisé avec GitHub" >> veille.log \
  || echo "[git] ATTENTION : pull échoué, base peut-être périmée" >> veille.log

# Passerelle iPhone documents (photos/PDF de flyers) : le projet est hors iCloud
# (launchd exige un disque local), donc l'iPhone dépose dans un dossier iCloud à
# part, qu'on rapatrie ici avant que l'agent ne lise data/inbox_docs/.
ICLOUD_INBOX="$HOME/Library/Mobile Documents/com~apple~CloudDocs/RadarInbox"
if [ -d "$ICLOUD_INBOX" ]; then
  mkdir -p data/inbox_docs
  find "$ICLOUD_INBOX" -maxdepth 1 -type f ! -name '.*' -exec mv {} data/inbox_docs/ \;
fi

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
# À partir d'ici, plus AUCUNE erreur ne doit tuer le script : tout ce qui suit
# (remontée des propositions, rapport quotidien) sert précisément à signaler les
# pannes. Un `set -e` qui interrompt ici supprimerait le seul mécanisme d'alerte
# — c'est ce qui s'est produit le 16/08/2026 : verrou git orphelin, script mort
# avant l'envoi du mail, panne invisible pendant toute la matinée.
set +e

"$CLAUDE_BIN" -p "$(cat veille_agent.md)" \
  --allowedTools WebSearch WebFetch Read Write Edit Glob Grep "Bash(python3:*)" \
  --permission-mode acceptEdits \
  --add-dir "$PROJECT_DIR" \
  >> veille.log 2>&1
RC=$?    # code de sortie de l'AGENT, capturé immédiatement. Avant, RC=$? était
         # lu après le bloc « vider la note » et renvoyait le statut de ce bloc :
         # le « rc=0 » du journal ne disait donc rien de la veille elle-même.
if [ "$RC" -ne 0 ]; then
  echo "[veille] ATTENTION : l'agent de veille a échoué (code $RC)" >> veille.log
fi

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

echo "----- fin veille (rc=$RC) $(date '+%H:%M:%S') -----" >> veille.log

# Verrou git orphelin : piège déjà rencontré deux fois. Un `index.lock` laissé par
# une commande interrompue bloque toute écriture git jusqu'à suppression manuelle.
# On ne le retire QUE s'il est vieux (>30 min) ET qu'aucun git n'écrit réellement —
# `gitstatusd`, le daemon d'affichage du prompt, est en lecture seule et exclu.
if [ -f .git/index.lock ]; then
  if pgrep -x git >/dev/null 2>&1; then
    echo "[git] ATTENTION : index.lock présent ET un git tourne — verrou laissé en place" >> veille.log
  elif [ -z "$(find .git/index.lock -mmin +30 2>/dev/null)" ]; then
    echo "[git] ATTENTION : index.lock récent (<30 min), verrou laissé en place par prudence" >> veille.log
  else
    rm -f .git/index.lock
    echo "[git] ATTENTION : verrou orphelin index.lock supprimé (aucun git actif)" >> veille.log
  fi
fi

# Fait remonter les propositions vers la page de validation /admin (Replit) :
# commit + push de data/pending/ uniquement. Ce n'est PAS une publication —
# events.json et index.html ne sont pas touchés, le site public ne change pas.
# C'est simplement le canal Mac -> GitHub -> webhook /sync -> Replit.
# data/pistes_organisateurs.json suit le même canal : c'est de la matière de
# démarchage, pas une publication. Il est cumulatif et n'entre jamais dans
# events.json — il voyage ici uniquement pour être sauvegardé et partagé.
# Chaque étape est testée séparément : `git add` et `git commit` échouaient en
# silence sous set -e et emportaient tout le reste du script avec eux.
if [ -n "$(git status --porcelain data/pending/ data/pistes_organisateurs.json 2>/dev/null)" ]; then
  if ! git add data/pending/ data/pistes_organisateurs.json >> veille.log 2>&1; then
    echo "[git] ATTENTION : git add a échoué — propositions restées locales" >> veille.log
  elif ! git commit -q -m "veille $(date +%F) : propositions à valider" >> veille.log 2>&1; then
    echo "[git] ATTENTION : git commit a échoué — propositions restées locales" >> veille.log
  elif git push origin main >> veille.log 2>&1; then
    echo "[git] propositions envoyées vers /admin" >> veille.log
  else
    echo "[git] ATTENTION : push échoué, propositions restées locales" >> veille.log
  fi
else
  echo "[git] aucune nouvelle proposition à envoyer" >> veille.log
fi

# Rapport quotidien par mail (résumé + liens, ne publie rien).
if ./venv/bin/python daily_report.py >> veille.log 2>&1; then
  echo "[mail] rapport quotidien envoyé" >> veille.log
  MAIL_OK=1
else
  echo "[mail] ATTENTION : échec envoi rapport quotidien" >> veille.log
  MAIL_OK=0
fi

# Notification macOS. C'est le SEUL canal indépendant du mail : quand l'envoi
# échoue, c'est la seule chose qui puisse encore prévenir le jour même. Elle doit
# donc dire l'échec, pas annoncer une réussite (le rapport suivant, lui, signalera
# rétrospectivement les jours manquants).
LATEST=$(ls -t proposition_MAJ_*.md 2>/dev/null | head -1)
if [ "$MAIL_OK" = "1" ]; then
  NOTIF_TITRE="Radar Marchés — veille"
  NOTIF_TEXTE="Proposition prête : ${LATEST:-aucune}"
else
  NOTIF_TITRE="⚠️ Radar Marchés — RAPPORT NON ENVOYÉ"
  NOTIF_TEXTE="La veille a tourné mais le mail n'est pas parti. Voir veille.log."
fi
osascript -e "display notification \"$NOTIF_TEXTE\" with title \"$NOTIF_TITRE\"" 2>/dev/null || true

exit $RC
