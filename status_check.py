#!/usr/bin/env python3
"""
status_check.py — Recalcul DÉTERMINISTE des statuts selon la date du jour.

Les maths de dates sont faites en code (fiable, reproductible) ; le jugement
web est confié à l'agent Claude. Ce script n'écrit JAMAIS events.json : il
imprime un tableau Markdown que l'agent intègre à la proposition, et écrit
aussi les changements dans data/pending/status_AAAA-MM-JJ.json pour publier.py.

Usage : python status_check.py         # imprime le Markdown sur stdout
"""
from __future__ import annotations

from datetime import date, timedelta

import common as C


def recompute(events, today: date):
    props = []
    for e in events:
        cur = e.get("status")
        if cur == "perm":
            continue
        new = reason = None

        dl_dates = C.parse_dates_from_text(e.get("deadline", ""))
        past_dl = [d for d in dl_dates if d < today]
        future_dl = [d for d in dl_dates if d >= today]

        if cur in ("open", "soon") and past_dl and not future_dl:
            recent = max(past_dl)
            if e.get("dateStatus") in ("annuel", "récurrent"):
                if cur == "open":
                    new, reason = "soon", "date limite passée — surveiller l'édition suivante"
            elif recent.year >= today.year:
                new, reason = "closed", f"date limite dépassée ({recent.isoformat()})"

        if cur == "closed" and e.get("dateStatus") in ("annuel", "récurrent", "confirmée"):
            m = e.get("month")
            if isinstance(m, int) and 1 <= m <= 12:
                if date(today.year, m, 1) < today - timedelta(days=31):
                    new, reason = "soon", "édition passée — surveiller l'appel de l'édition suivante"

        if new and new != cur:
            props.append({"key": C.event_key(e), "name": e["name"], "zone": e["zone"],
                          "from": cur, "to": new, "reason": reason})
    return props


def main():
    today = date.today()
    events = C.load_json(C.EVENTS_JSON, [])
    props = recompute(events, today)

    # fichier machine pour publier.py --apply
    C.PENDING.mkdir(parents=True, exist_ok=True)
    C.save_json_atomic(C.PENDING / f"status_{today.isoformat()}.json", {"status_changes": props})

    # markdown pour l'agent
    print(f"### Changements de statut déterministes ({today.isoformat()})\n")
    if not props:
        print("_Aucun changement suggéré par les dates cette semaine._")
        return
    print("| Événement | Zone | Actuel | Proposé | Motif |")
    print("|---|---|---|---|---|")
    for p in props:
        print(f"| {p['name']} | {p['zone']} | `{p['from']}` | **`{p['to']}`** | {p['reason']} |")


if __name__ == "__main__":
    main()
