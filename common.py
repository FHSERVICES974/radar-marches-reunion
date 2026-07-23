#!/usr/bin/env python3
"""
common.py — Fonctions partagées par veille.py et publier.py.

Aucune de ces fonctions n'écrit jamais dans events.json de façon automatique
sans passer par une sauvegarde préalable. La règle du projet est stricte :
la veille PROPOSE, l'humain VALIDE, publier.py APPLIQUE.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import unicodedata
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
BACKUPS = DATA / "backups"
PENDING = DATA / "pending"
EVENTS_JSON = DATA / "events.json"
ORGS_JSON = DATA / "orgs.json"
META_JSON = DATA / "meta.json"
COMMUNITY_JSON = DATA / "community_inbox.json"

ZONES = {"Nord", "Est", "Ouest", "Sud", "National"}

# Mois FR -> numéro, pour parser les dates en texte libre.
_MOIS = {
    "janvier": 1, "janv": 1, "fevrier": 2, "fev": 2, "mars": 3, "avril": 4,
    "mai": 5, "juin": 6, "juillet": 7, "juil": 7, "aout": 8, "septembre": 9,
    "sept": 9, "octobre": 10, "oct": 10, "novembre": 11, "nov": 11,
    "decembre": 12, "dec": 12,
}


# ---------------------------------------------------------------- I/O JSON
def load_json(path: Path, default=None):
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def save_json_atomic(path: Path, data) -> None:
    """Écriture atomique : on écrit dans un .tmp puis on remplace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    tmp.replace(path)


def backup_events() -> Path:
    """Copie horodatée de events.json avant toute écriture."""
    BACKUPS.mkdir(parents=True, exist_ok=True)
    dst = BACKUPS / f"events_{today_iso()}.json"
    # Si plusieurs sauvegardes le même jour, on suffixe pour ne rien écraser.
    i = 1
    while dst.exists():
        dst = BACKUPS / f"events_{today_iso()}_{i}.json"
        i += 1
    shutil.copy2(EVENTS_JSON, dst)
    return dst


# ---------------------------------------------------------------- dates
def today_iso() -> str:
    return date.today().isoformat()


def norm(s: str) -> str:
    """Normalise pour comparaison/dédup : minuscules, sans accents, sans ponctuation."""
    if not s:
        return ""
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return s.strip()


def event_key(e: dict) -> str:
    """Clé de dédup : nom + zone normalisés."""
    return norm(e.get("name", "")) + "|" + norm(e.get("zone", ""))


def parse_dates_from_text(text: str):
    """Extrait les dates plausibles d'un texte libre (deadline/when).

    Reconnaît : JJ/MM/AAAA, AAAA-MM-JJ, « JJ mois AAAA », « mois AAAA ».
    Renvoie la liste des objets date trouvés (peut être vide)."""
    if not text:
        return []
    found = []
    t = text

    # JJ/MM/AAAA ou JJ-MM-AAAA
    for m in re.finditer(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})\b", t):
        d, mo, y = int(m[1]), int(m[2]), int(m[3])
        _try_add(found, y, mo, d)

    # AAAA-MM-JJ
    for m in re.finditer(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", t):
        y, mo, d = int(m[1]), int(m[2]), int(m[3])
        _try_add(found, y, mo, d)

    # « JJ mois AAAA » (ex: 30 juin 2026, 29 mai 2026)
    low = norm(t)
    for m in re.finditer(r"\b(\d{1,2})\s+([a-z]+)\s+(\d{4})\b", low):
        d = int(m[1]); mo = _MOIS.get(m[2][:4]) or _MOIS.get(m[2]); y = int(m[3])
        if mo:
            _try_add(found, y, mo, d)

    # « mois AAAA » sans jour -> 1er du mois
    for m in re.finditer(r"\b([a-z]{3,})\s+(\d{4})\b", low):
        mo = _MOIS.get(m[1][:4]) or _MOIS.get(m[1]); y = int(m[2])
        if mo:
            _try_add(found, y, mo, 1)

    return sorted(set(found))


def _try_add(acc, y, mo, d):
    # Borne les années à une plage plausible pour éviter les faux positifs
    # comme « L.2122-1-1 » (référence d'article de loi) lu comme l'an 2122.
    if not (2020 <= y <= date.today().year + 3):
        return
    try:
        acc.append(date(y, mo, d))
    except ValueError:
        pass


# ---------------------------------------------------------------- notifications
def macos_notify(title: str, message: str) -> None:
    """Notification native macOS (best-effort, sans dépendance)."""
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification {json.dumps(message)} with title {json.dumps(title)}'],
            check=False, capture_output=True,
        )
    except Exception:
        pass


# ---------------------------------------------------------------- git helpers
def git(*args, cwd: Path = ROOT, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), check=check,
                          capture_output=True, text=True)


def is_git_repo(cwd: Path = ROOT) -> bool:
    r = git("rev-parse", "--is-inside-work-tree", cwd=cwd, check=False)
    return r.returncode == 0 and r.stdout.strip() == "true"
