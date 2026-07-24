#!/usr/bin/env python3
"""
publier.py — Publication APRÈS validation humaine.

Étapes :
  1. (option) --apply : applique une proposition curée
       data/pending/pending_MAJ_AAAA-MM-JJ.json  (changements de statut +
       nouveaux événements complétés à la main) sur events.json, APRÈS backup.
  2. Met META.lastUpdate à la date du jour (data/meta.json).
  3. Régénère index.html via build.py.
  4. git add / commit / push (si dépôt + remote configurés).
  5. Déclenche le redéploiement Replit :
       - Option B : POST sur REPLIT_DEPLOY_HOOK (défini dans .env) si présent.
       - Sinon Option A : rappel « Redeploy » manuel dans l'onglet Deployments.

Usage :
  python publier.py                          # backup + lastUpdate=today + build + push + redeploy
  python publier.py --apply data/pending/pending_MAJ_2026-07-28.json
  python publier.py --no-push                # tout sauf git push (test local)
  python publier.py --set-date 2026-07-28    # forcer une date de MAJ précise
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*a, **k):
        return False

import common as C
import build as B

load_dotenv(C.ROOT / ".env")

REQUIRED_EVENT_KEYS = B.EVENT_KEYS


# ------------------------------------------------------------------ apply
def apply_pending(pending_path: Path, events: list) -> list:
    payload = C.load_json(pending_path)
    if not payload:
        raise SystemExit(f"Fichier pending illisible : {pending_path}")

    by_key = {C.event_key(e): e for e in events}

    # 1) changements de statut conservés
    applied_status = 0
    for ch in payload.get("status_changes", []):
        e = by_key.get(ch.get("key"))
        if e and e.get("status") != ch.get("to"):
            e["status"] = ch["to"]
            applied_status += 1
    print(f"[apply] {applied_status} changement(s) de statut appliqué(s)")

    # 2) nouveaux événements complétés à la main (champ 'event' rempli)
    added = 0
    for cand in payload.get("new_events_candidates", []):
        ev = cand.get("event")
        if not ev:
            continue  # non complété -> ignoré
        missing = [k for k in REQUIRED_EVENT_KEYS if k not in ev]
        if missing:
            print(f"[apply] IGNORÉ « {ev.get('name','?')} » : clés manquantes {missing}",
                  file=sys.stderr)
            continue
        k = C.event_key(ev)
        if k in by_key:
            print(f"[apply] DOUBLON ignoré : {ev.get('name')}")
            continue
        events.append(ev)
        by_key[k] = ev
        added += 1
    print(f"[apply] {added} nouvel(aux) événement(s) ajouté(s)")

    # 3) remontées communauté avec objet 'event' complet
    for c in payload.get("community", []):
        ev = c.get("event") if isinstance(c, dict) else None
        if ev and all(k in ev for k in REQUIRED_EVENT_KEYS):
            k = C.event_key(ev)
            if k not in by_key:
                events.append(ev); by_key[k] = ev
                print(f"[apply] communauté ajoutée : {ev.get('name')}")
    return events


# ------------------------------------------------------------------ auto (Niveau 1)
from urllib.parse import urlparse  # noqa: E402

AUTO_MAX = int(os.getenv("AUTO_MAX", "5"))  # plafond anti-anomalie / run


def _host(url: str) -> str:
    try:
        return (urlparse(url).netloc or "").lower().replace("www.", "")
    except Exception:
        return ""


def official_domains() -> set:
    """Whitelist d'auto-publication = domaines institutionnels du registre
    (tier1 + les 24 communes). Seul un item sourcé sur ces domaines part tout seul."""
    src = C.load_json(C.ROOT / "data" / "sources.json", {}) or {}
    hosts = set()
    t1 = src.get("tier1_institutionnel", {})
    for s in t1.get("sources", []):
        h = _host(s.get("url", ""))
        if h:
            hosts.add(h)
    for c in t1.get("toutes_communes_974", {}).get("communes", []):
        h = _host(c.get("url", ""))
        if h:
            hosts.add(h)
    return hosts


def find_latest_pending() -> Path | None:
    files = sorted(C.PENDING.glob("pending_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def apply_auto(pending_path: Path, events: list):
    """Applique SEULEMENT la part à haute confiance :
       - tous les changements de statut (déterministes, sans scraping)
       - les nouveaux 'Vérifié' dont la source est un domaine institutionnel whitelisté
       - dans la limite du plafond AUTO_MAX
    Le reste est laissé en attente (retourné dans 'held')."""
    payload = C.load_json(pending_path) or {}
    whitelist = official_domains()
    by = {C.event_key(e): e for e in events}
    published, held = [], []

    # 1) statuts déterministes -> toujours sûrs
    n_status = 0
    for ch in payload.get("status_changes", []):
        e = by.get(ch.get("key"))
        if e and e.get("status") != ch.get("to"):
            e["status"] = ch["to"]; n_status += 1

    # 2) nouveaux 'Vérifié' institutionnels, plafonnés
    for cand in payload.get("new_events_candidates", []):
        ev = cand.get("event")
        conf = (cand.get("_confidence") or "").strip().lower()
        url = cand.get("_source_url", "") or (ev or {}).get("url", "")
        reason = None
        if not ev:
            reason = "non complété (event=null)"
        elif conf != "vérifié" and conf != "verifie":
            reason = f"confiance '{cand.get('_confidence')}' (pas Vérifié)"
        elif not any(_host(url) == d or _host(url).endswith("." + d)
                     for d in whitelist):
            reason = f"source non institutionnelle ({_host(url) or 'sans url'})"
        elif [k for k in REQUIRED_EVENT_KEYS if k not in ev]:
            reason = "objet event incomplet"
        elif C.event_key(ev) in by:
            reason = "doublon"
        elif len(published) >= AUTO_MAX:
            reason = f"plafond AUTO_MAX={AUTO_MAX} atteint"

        if reason:
            held.append({"name": (ev or cand).get("name") or cand.get("_source_title", "?"),
                         "reason": reason})
        else:
            events.append(ev); by[C.event_key(ev)] = ev
            published.append({"name": ev["name"], "source": _host(url)})

    # tout ce qui est communauté/social -> jamais auto
    for c in payload.get("community", []):
        held.append({"name": (c or {}).get("name", "?"), "reason": "communauté/social (jamais auto)"})

    return events, published, held, n_status


def rollback():
    """Restaure la dernière sauvegarde de events.json puis rebuild."""
    bks = sorted(C.BACKUPS.glob("events_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not bks:
        raise SystemExit("[rollback] Aucune sauvegarde trouvée dans data/backups/.")
    src = bks[0]
    import shutil
    shutil.copy2(src, C.EVENTS_JSON)
    print(f"[rollback] events.json restauré depuis {src.name}")
    B.build(check_only=False)
    print("[rollback] index.html régénéré. (git : committez/poussez si déjà en ligne.)")


# ------------------------------------------------------------------ git + replit
def git_publish(commit_date: str, no_push: bool):
    if not C.is_git_repo():
        print("[git] Pas un dépôt git. Initialisez d'abord :")
        print("      cd radar-marches && git init && git add . && "
              "git commit -m init && git remote add origin <URL_GITHUB> && "
              "git push -u origin main")
        return False
    C.git("add", "-A")
    status = C.git("status", "--porcelain")
    if not status.stdout.strip():
        print("[git] Aucun changement à committer.")
        return True
    C.git("commit", "-m", f"MAJ auto {commit_date}")
    print(f"[git] Commit « MAJ auto {commit_date} » créé.")
    if no_push:
        print("[git] --no-push : push ignoré.")
        return True
    has_remote = C.git("remote", check=False).stdout.strip()
    if not has_remote:
        print("[git] Aucun remote configuré. `git remote add origin <URL>` puis `git push`.")
        return False
    r = C.git("push", check=False)
    if r.returncode != 0:
        print(f"[git] push échoué :\n{r.stderr}", file=sys.stderr)
        return False
    print("[git] Poussé vers GitHub.")
    return True


def trigger_replit():
    hook = os.getenv("REPLIT_DEPLOY_HOOK", "").strip()
    if not hook:
        print("\n[replit] OPTION A — redéploiement manuel :")
        print("         Ouvrez Replit > onglet Deployments > bouton « Redeploy ».")
        print("         (Astuce : renseignez REPLIT_DEPLOY_HOOK dans .env pour automatiser — option B.)")
        return
    try:
        import requests
        r = requests.post(hook, timeout=20)
        if 200 <= r.status_code < 300:
            print(f"[replit] Deploy Hook déclenché (HTTP {r.status_code}). Redéploiement en cours.")
        else:
            print(f"[replit] Deploy Hook : HTTP {r.status_code} — vérifiez l'URL.", file=sys.stderr)
    except Exception as ex:
        print(f"[replit] Échec appel Deploy Hook : {ex}", file=sys.stderr)
        print("[replit] Repli sur OPTION A : Redeploy manuel dans l'onglet Deployments.")


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description="Publie le site après validation.")
    ap.add_argument("--apply", metavar="PENDING_JSON",
                    help="Applique une proposition curée sur events.json")
    ap.add_argument("--auto", nargs="?", const="__latest__", metavar="PENDING_JSON",
                    help="Niveau 1 : auto-publie statuts + nouveaux 'Vérifié' institutionnels "
                         "(plafonné). Sans argument, prend le dernier pending_*.json.")
    ap.add_argument("--rollback", action="store_true",
                    help="Restaure la dernière sauvegarde de events.json + rebuild")
    ap.add_argument("--no-push", action="store_true", help="Ne pas git push")
    ap.add_argument("--set-date", metavar="AAAA-MM-JJ", help="Forcer la date de MAJ")
    args = ap.parse_args()

    if args.rollback:
        rollback()
        git_publish(date.today().isoformat() + " (rollback)", no_push=args.no_push)
        trigger_replit()
        return

    upd = args.set_date or date.today().isoformat()

    # 1bis. auto (Niveau 1) — porte de confiance, sans intervention humaine
    if args.auto:
        pending_path = (find_latest_pending() if args.auto == "__latest__"
                        else ((C.ROOT / args.auto) if not os.path.isabs(args.auto) else Path(args.auto)))
        if not pending_path or not pending_path.exists():
            raise SystemExit("[auto] Aucun fichier pending trouvé. Lancez la veille d'abord.")
        bkp = C.backup_events()
        print(f"[backup] events.json -> {bkp.relative_to(C.ROOT)}")
        events = C.load_json(C.EVENTS_JSON, [])
        events, published, held, n_status = apply_auto(pending_path, events)
        C.save_json_atomic(C.EVENTS_JSON, events)
        print(f"[auto] {n_status} statut(s) · {len(published)} publié(s) auto · {len(held)} en attente")
        for p in published:
            print(f"   ✅ {p['name']}  ({p['source']})")
        for h in held[:12]:
            print(f"   ⏸️  {h['name']} — {h['reason']}")
        C.macos_notify("Radar Marchés — publication auto",
                       f"{len(published)} publié(s) auto · {len(held)} en attente de validation")

    # 1. apply (avec backup préalable obligatoire)
    if args.apply:
        pending_path = (C.ROOT / args.apply) if not os.path.isabs(args.apply) else Path(args.apply)
        if not pending_path.exists():
            raise SystemExit(f"Introuvable : {pending_path}")
        bkp = C.backup_events()
        print(f"[backup] events.json -> {bkp.relative_to(C.ROOT)}")
        events = C.load_json(C.EVENTS_JSON, [])
        events = apply_pending(pending_path, events)
        C.save_json_atomic(C.EVENTS_JSON, events)
        print(f"[apply] events.json enregistré ({len(events)} événements).")

    # 2. META.lastUpdate
    meta = C.load_json(C.META_JSON, {}) or {}
    meta["lastUpdate"] = upd
    C.save_json_atomic(C.META_JSON, meta)
    print(f"[meta] lastUpdate = {upd}")

    # 3. build
    B.build(check_only=False)

    # 4. git
    git_publish(upd, no_push=args.no_push)

    # 5. replit
    trigger_replit()
    print("\n[publier] Terminé.")


if __name__ == "__main__":
    main()
