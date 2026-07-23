#!/usr/bin/env python3
"""
build.py — Régénère index.html à partir de template.html + des données JSON.

Le design (CSS / HTML / logique JS) vit dans template.html et n'est JAMAIS
modifié par ce script. Seuls trois placeholders sont remplacés :
    __EVENTS__      -> data/events.json
    __ORGS__        -> data/orgs.json
    __LASTUPDATE__  -> data/meta.json ["lastUpdate"]

Usage :
    python build.py                 # build normal -> index.html
    python build.py --check         # build en mémoire + vérifie, n'écrit rien
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TEMPLATE = ROOT / "template.html"
DATA = ROOT / "data"
EVENTS_JSON = DATA / "events.json"
ORGS_JSON = DATA / "orgs.json"
META_JSON = DATA / "meta.json"
OUTPUT = ROOT / "index.html"

# Ordre exact des clés attendu pour un événement (documentaire / validation douce).
EVENT_KEYS = [
    "name", "zone", "type", "org", "place", "when", "badge", "month",
    "dateStatus", "status", "deadline", "contact", "social", "url", "apply", "desc",
]


def _load_json(path: Path):
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _js_literal(data) -> str:
    """Sérialise en littéral JS sûr à injecter dans <script>.

    JSON est un sous-ensemble de JS : la valeur produite est parsée à
    l'identique par le navigateur. On échappe '</' pour qu'aucune valeur ne
    puisse fermer prématurément la balise <script> (ex: '</script>')."""
    txt = json.dumps(data, ensure_ascii=False, indent=2)
    return txt.replace("</", "<\\/")


def build(check_only: bool = False) -> str:
    template = TEMPLATE.read_text(encoding="utf-8")
    events = _load_json(EVENTS_JSON)
    orgs = _load_json(ORGS_JSON)
    meta = _load_json(META_JSON)

    # Validations minimales (n'altèrent rien, protègent juste contre un JSON cassé).
    if not isinstance(events, list) or not events:
        raise SystemExit("events.json vide ou invalide")
    if not isinstance(orgs, list) or not orgs:
        raise SystemExit("orgs.json vide ou invalide")
    last_update = str(meta.get("lastUpdate", "")).strip()
    if not last_update:
        raise SystemExit("meta.json: lastUpdate manquant")

    for i, e in enumerate(events):
        missing = [k for k in EVENT_KEYS if k not in e]
        if missing:
            raise SystemExit(f"events.json[{i}] ({e.get('name','?')}): clés manquantes {missing}")

    for token in ("__EVENTS__", "__ORGS__", "__LASTUPDATE__"):
        if token not in template:
            raise SystemExit(f"template.html: placeholder {token} introuvable")

    html = (
        template
        .replace("__EVENTS__", _js_literal(events))
        .replace("__ORGS__", _js_literal(orgs))
        .replace("__LASTUPDATE__", last_update)
    )

    if not check_only:
        OUTPUT.write_text(html, encoding="utf-8")
        # Miroir déployé sur Replit : index.html EST la page servie.
        print(f"[build] index.html régénéré — {len(events)} événements, "
              f"{len(orgs)} organisateurs, MAJ {last_update}")
    return html


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Régénère index.html depuis template + JSON.")
    ap.add_argument("--check", action="store_true", help="Vérifie sans écrire index.html")
    args = ap.parse_args()
    build(check_only=args.check)
    if args.check:
        print("[build] --check OK (aucun fichier écrit)")
