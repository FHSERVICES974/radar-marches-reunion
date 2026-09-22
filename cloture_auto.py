#!/usr/bin/env python3
"""
cloture_auto.py — Ferme automatiquement les candidatures dont la date est passée.

Décision de François (22/09/2026) : ces fermetures s'appliquent seules, sans
validation. Ce n'est pas une information captée sur le web (« capté ≠ publié »
reste la règle pour tout le reste) : c'est un calcul de date sur la fiche
elle-même. Auparavant, status_check.py les calculait chaque nuit dans un
fichier que ni /admin ni personne ne lisait — 15 fiches échues restaient
« ouvertes » dans les données.

Périmètre VOLONTAIREMENT ÉTROIT : seules les fiches au statut « open » sont
touchées, selon les règles de status_check.recompute() :
  - open -> closed : date limite (ou, à défaut, date de l'événement) passée ;
  - open -> soon   : fiche annuelle dont la date limite est passée
                     (candidature close, prochaine édition à surveiller).
Aucune autre transition (closed -> soon, etc.) n'est appliquée ici.

Enchaîne : sauvegarde de events.json -> statuts -> meta.lastUpdate -> build ->
commit des SEULS fichiers du site -> push. Écrit la liste des fiches fermées
dans data/pending/clotures_AAAA-MM-JJ.json pour le rapport du matin.

Usage : python cloture_auto.py [--no-push]
"""
from __future__ import annotations

import sys
from datetime import date

import build as B
import common as C
import status_check as S

FICHIERS_SITE = ["data/events.json", "data/meta.json", "index.html", "sitemap.xml"]


def _pousser() -> bool:
    """Push, avec UN nouvel essai après resynchronisation : /admin peut avoir
    poussé entre-temps (course déjà rencontrée sur meta.json)."""
    if C.git("push", "-q", "origin", "main", check=False).returncode == 0:
        return True
    r = C.git("pull", "-q", "--rebase", "--autostash", "origin", "main", check=False)
    if r.returncode != 0:
        C.git("rebase", "--abort", check=False)
        print(f"[cloture] ATTENTION : resynchronisation impossible ({r.stderr.strip()[:200]})")
        return False
    return C.git("push", "-q", "origin", "main", check=False).returncode == 0


def main(no_push: bool = False) -> int:
    aujourd_hui = date.today()          # heure de La Réunion : TZ exporté par run_veille.sh
    events = C.load_json(C.EVENTS_JSON, [])
    props = [p for p in S.recompute(events, aujourd_hui)
             if p["from"] == "open" and p["to"] in ("closed", "soon")]
    if not props:
        print("[cloture] aucune candidature à fermer")
        return 0

    bkp = C.backup_events()
    par_cle = {C.event_key(e): e for e in events}
    faites = []
    for p in props:
        e = par_cle.get(p["key"])
        if e and e.get("status") == "open":
            e["status"] = p["to"]
            faites.append({"name": e["name"], "zone": e.get("zone", ""),
                           "to": p["to"], "reason": p["reason"]})
    if not faites:
        print("[cloture] aucune candidature à fermer")
        return 0

    C.save_json_atomic(C.EVENTS_JSON, events)
    meta = C.load_json(C.META_JSON, {}) or {}
    meta["lastUpdate"] = aujourd_hui.isoformat()
    C.save_json_atomic(C.META_JSON, meta)
    B.build(check_only=False)

    # Trace pour le rapport du matin (cumulée si le script passe deux fois).
    trace = C.PENDING / f"clotures_{aujourd_hui.isoformat()}.json"
    deja = C.load_json(trace, []) or []
    C.save_json_atomic(trace, deja + faites)

    for f in faites:
        print(f"[cloture] {f['name']} -> {f['to']} ({f['reason']})")
    print(f"[cloture] {len(faites)} candidature(s) fermée(s) — sauvegarde : {bkp.name}")

    if not C.is_git_repo():
        return 0
    C.git("add", *FICHIERS_SITE)
    if not C.git("status", "--porcelain", *FICHIERS_SITE).stdout.strip():
        return 0
    C.git("commit", "-q", "-m",
          f"Clôture automatique {aujourd_hui.isoformat()} : {len(faites)} "
          f"candidature(s) dont la date est passée")
    if no_push:
        print("[cloture] --no-push : commit local seulement")
    elif _pousser():
        print("[cloture] publié (site mis à jour)")
    else:
        print("[cloture] ATTENTION : push échoué — fermetures restées locales")
    return 0


if __name__ == "__main__":
    sys.exit(main(no_push="--no-push" in sys.argv))
