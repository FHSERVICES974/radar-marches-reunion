#!/usr/bin/env python3
"""
marquer_veille.py — Date la dernière veille RÉUSSIE sur le site.

Appelé par run_veille.sh uniquement quand la veille a abouti. Écrit
meta.json["lastCheck"], régénère la page et publie. Le site affiche alors
« Vérifié le … · dernière modification le … » : la première date avance chaque
nuit, la seconde seulement quand une fiche change.

Si la veille échoue, ce script n'est pas appelé et la date reste figée : un
visiteur voit que la vérification date — c'est voulu, c'est un signal honnête.

Usage : python marquer_veille.py [--no-push]
"""
from __future__ import annotations

import sys
from datetime import date

import build as B
import common as C
import cloture_auto as CA


def main(no_push: bool = False) -> int:
    aujourd_hui = date.today().isoformat()      # heure de La Réunion (TZ exporté)
    meta = C.load_json(C.META_JSON, {}) or {}
    if meta.get("lastCheck") == aujourd_hui:
        print("[verif] date de vérification déjà à jour")
        return 0
    meta["lastCheck"] = aujourd_hui
    C.save_json_atomic(C.META_JSON, meta)
    B.build(check_only=False)
    print(f"[verif] site daté : vérifié le {aujourd_hui}")

    if not C.is_git_repo():
        return 0
    C.git("add", *CA.FICHIERS_SITE)
    if not C.git("status", "--porcelain", *CA.FICHIERS_SITE).stdout.strip():
        return 0
    C.git("commit", "-q", "-m", f"Veille {aujourd_hui} : site vérifié")
    if no_push:
        print("[verif] --no-push : commit local seulement")
    elif CA._pousser():
        print("[verif] publié")
    else:
        print("[verif] ATTENTION : push échoué — date restée locale")
    return 0


if __name__ == "__main__":
    sys.exit(main(no_push="--no-push" in sys.argv))
