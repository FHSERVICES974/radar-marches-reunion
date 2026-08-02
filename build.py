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
SITEMAP = ROOT / "sitemap.xml"

SITE_URL = "https://radar.artisanspei.re"

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


_MOIS_FR = {
    "janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
    "juillet": 7, "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11,
    "decembre": 12,
}


def _strict_date(text: str):
    """Extrait une date SEULEMENT si jour + mois + année sont tous explicites.

    Volontairement plus strict que common.parse_dates_from_text, qui a un repli
    « mois AAAA -> 1er du mois » : ce repli produit une date plausible mais
    fausse (« 15 août 2026 » était ressorti en 2026-08-01). Pour du JSON-LD
    exposé à Google, mieux vaut aucune date qu'une date inexacte.
    """
    import re
    import unicodedata
    from datetime import date

    if not text:
        return None
    t = unicodedata.normalize("NFD", text)
    t = "".join(c for c in t if unicodedata.category(c) != "Mn").lower()

    # Plage « du 12 au 15 septembre 2026 » / « 12-15 septembre 2026 » :
    # startDate = le PREMIER jour. Testé avant le motif simple, sinon la regex
    # ci-dessous capterait « 15 septembre 2026 », soit la date de FIN.
    m = re.search(r"\b(\d{1,2})\s*(?:au|[-–—à])\s*(\d{1,2})\s+([a-z]+)\s+(\d{4})\b", t)
    if m:
        mo = _MOIS_FR.get(m.group(3))
        if mo:
            try:
                return date(int(m.group(4)), mo, int(m.group(1)))
            except ValueError:
                return None

    # JJ mois AAAA (ex: « samedi 15 aout 2026 »)
    m = re.search(r"\b(\d{1,2})\s+([a-z]+)\s+(\d{4})\b", t)
    if m:
        mo = _MOIS_FR.get(m.group(2))
        if mo:
            try:
                return date(int(m.group(3)), mo, int(m.group(1)))
            except ValueError:
                return None

    # AAAA-MM-JJ
    m = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", t)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None

    return None


def _jsonld_events(events: list) -> str:
    """Génère le bloc JSON-LD schema.org/Event (invisible, pour Google).

    Règle stricte, cohérente avec « capté ≠ publié » : on n'invente JAMAIS de
    date. `startDate` n'est émis que si dateStatus == "confirmée" ET qu'une date
    complète (jour+mois+année) est extractible du texte. Sinon on l'omet —
    Google pénalise une donnée structurée fausse bien plus qu'une absente.
    """

    nodes = []
    for e in events:
        if e.get("status") == "closed":
            continue

        node = {
            "@type": "Event",
            "name": e.get("name", ""),
            "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
            "eventStatus": "https://schema.org/EventScheduled",
        }
        if e.get("desc"):
            node["description"] = e["desc"]
        if e.get("place"):
            node["location"] = {"@type": "Place", "name": e["place"],
                                "address": {"@type": "PostalAddress",
                                            "addressRegion": "La Réunion",
                                            "addressCountry": "RE"}}
        if e.get("org"):
            node["organizer"] = {"@type": "Organization", "name": e["org"]}
        if e.get("url"):
            node["url"] = e["url"]

        # Date : uniquement si explicitement confirmée et réellement parsable.
        if str(e.get("dateStatus", "")).strip().lower() == "confirmée":
            d = _strict_date(e.get("when", ""))
            if d:
                node["startDate"] = d.isoformat()

        nodes.append(node)

    if not nodes:
        return ""
    graph = {"@context": "https://schema.org", "@graph": nodes}
    txt = json.dumps(graph, ensure_ascii=False, indent=2).replace("</", "<\\/")
    return f'<script type="application/ld+json">\n{txt}\n</script>'


def _write_sitemap(last_update: str) -> None:
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>{SITE_URL}/</loc>
    <lastmod>{last_update}</lastmod>
    <changefreq>daily</changefreq>
    <priority>1.0</priority>
  </url>
  <url>
    <loc>{SITE_URL}/organisateurs</loc>
    <lastmod>{last_update}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.6</priority>
  </url>
</urlset>
"""
    SITEMAP.write_text(xml, encoding="utf-8")


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

    for token in ("__EVENTS__", "__ORGS__", "__LASTUPDATE__", "__JSONLD__"):
        if token not in template:
            raise SystemExit(f"template.html: placeholder {token} introuvable")

    jsonld = _jsonld_events(events)

    html = (
        template
        .replace("__EVENTS__", _js_literal(events))
        .replace("__ORGS__", _js_literal(orgs))
        .replace("__LASTUPDATE__", last_update)
        .replace("__JSONLD__", jsonld)
    )

    if not check_only:
        OUTPUT.write_text(html, encoding="utf-8")
        _write_sitemap(last_update)
        # Miroir déployé sur Replit : index.html EST la page servie.
        print(f"[build] index.html régénéré — {len(events)} événements, "
              f"{len(orgs)} organisateurs, MAJ {last_update}")
        print(f"[build] sitemap.xml écrit · JSON-LD : {jsonld.count('\"@type\": \"Event\"')} événements indexables")
    return html


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Régénère index.html depuis template + JSON.")
    ap.add_argument("--check", action="store_true", help="Vérifie sans écrire index.html")
    args = ap.parse_args()
    build(check_only=args.check)
    if args.check:
        print("[build] --check OK (aucun fichier écrit)")
