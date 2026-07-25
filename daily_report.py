#!/usr/bin/env python3
"""
daily_report.py — Compose et envoie le rapport quotidien après la veille.

Lit la proposition du jour (data/pending/pending_MAJ_AAAA-MM-JJ.json), résume
ce qui est actionnable, et envoie un mail via Mail.app (AppleScript, aucun
identifiant SMTP à gérer — cohérent avec le reste du pipeline).

N'écrit rien, ne publie rien : simple lecture + envoi de mail.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from common import ROOT, event_key, load_json, today_iso

DEST_EMAIL = "shadowneox@gmail.com"


def build_report() -> tuple[str, str] | None:
    today = today_iso()
    pending_path = ROOT / "data" / "pending" / f"pending_MAJ_{today}.json"
    if not pending_path.exists():
        return None

    d = load_json(pending_path, default={})
    status_changes = d.get("status_changes", [])
    candidates = d.get("new_events_candidates", [])
    community = d.get("community", [])

    # data/pending_decisions.json (qui trace Publier/Rejeter dans /admin) n'est
    # jamais committé sur GitHub — donc invisible depuis le Mac, et remis à zéro
    # à chaque republication côté Replit. Source fiable : est-ce que l'événement
    # existe déjà dans events.json (le seul état qui compte réellement) ?
    published_keys = {event_key(e) for e in load_json(ROOT / "data" / "events.json", default=[])}
    candidates = [
        c for c in candidates
        if not (c.get("event") and event_key(c["event"]) in published_keys)
    ]

    verifies = [c for c in candidates if c.get("_confidence") == "Vérifié" and c.get("event")]
    probables = [c for c in candidates if c.get("_confidence") != "Vérifié" or not c.get("event")]

    subject = f"Radar Marchés — veille du {today} : {len(verifies)} vérifié(s), {len(probables)} à revoir"

    lines = [
        f"Rapport de veille — {today}",
        "",
        f"{len(verifies)} appel(s) vérifié(s), prêt(s) à publier dans /admin.",
        f"{len(probables)} piste(s) à confirmer avant publication.",
        f"{len(status_changes)} changement(s) de statut détecté(s).",
        f"{len(community)} remontée(s) communautaire(s).",
        "",
    ]

    if verifies:
        lines.append("--- Prêts à publier ---")
        for c in verifies:
            ev = c["event"]
            deadline = ev.get("deadline") or "(pas de deadline précisée)"
            lines.append(f"• {ev.get('name','?')} — {ev.get('place','?')} — deadline : {deadline}")
        lines.append("")

    if probables:
        lines.append("--- À vérifier avant de publier ---")
        for c in probables:
            title = (c.get("event") or {}).get("name") or c.get("_source_title") or "(sans titre)"
            lines.append(f"• {title} — confiance : {c.get('_confidence','?')}")
        lines.append("")

    lines.append("Tout se valide dans /admin : https://radar.artisanspei.re/admin")
    lines.append("")
    lines.append(f"Rapport détaillé (Mac) : proposition_MAJ_{today}.md")

    return subject, "\n".join(lines)


def _applescript_escape(s: str) -> str:
    # AppleScript double-quoted strings only need backslash and quote escaped;
    # literal newlines inside the quotes are preserved as-is.
    return s.replace("\\", "\\\\").replace('"', '\\"')


def send_mail(subject: str, body: str) -> None:
    script = f'''
set theSubject to "{_applescript_escape(subject)}"
set theBody to "{_applescript_escape(body)}"
tell application "Mail"
  set newMsg to make new outgoing message with properties {{subject:theSubject, content:theBody, visible:false}}
  tell newMsg
    make new to recipient with properties {{address:"{DEST_EMAIL}"}}
  end tell
  send newMsg
end tell
'''
    subprocess.run(["osascript", "-e", script], check=True)


if __name__ == "__main__":
    report = build_report()
    if report is None:
        print("[daily_report] aucune proposition du jour — rien à envoyer.")
        sys.exit(0)
    subject, body = report
    send_mail(subject, body)
    print(f"[daily_report] envoyé à {DEST_EMAIL} : {subject}")
