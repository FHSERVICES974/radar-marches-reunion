#!/usr/bin/env python3
"""
doc_to_text.py — Extrait le texte d'un document déposé dans data/inbox_docs/.

Gère : .docx / .odt (dézippage XML), .txt, et .pdf « texte » (via pdftotext si
présent). Pour un PDF SCANNÉ (image) ou une photo (.jpg/.png), le texte n'est
pas extractible ici → l'agent de veille le lit VISUELLEMENT avec l'outil Read.

Usage : python3 doc_to_text.py "chemin/vers/fichier.pdf"
Sortie : le texte sur stdout, ou une ligne [SCAN] indiquant une lecture visuelle.
"""
from __future__ import annotations

import html
import re
import subprocess
import sys
import zipfile
from pathlib import Path


def from_zip_xml(path: Path) -> str:
    """docx/odt : concatène le texte des XML internes."""
    parts = []
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist()
                 if n.endswith("document.xml") or n.startswith("word/")
                 or n.endswith("content.xml")]
        for n in names:
            if not n.endswith(".xml"):
                continue
            xml = z.read(n).decode("utf-8", "ignore")
            xml = re.sub(r"</w:p>|</text:p>", "\n", xml)
            xml = re.sub(r"<[^>]+>", "", xml)
            parts.append(html.unescape(xml))
    txt = "\n".join(parts)
    return re.sub(r"\n\s*\n+", "\n\n", txt).strip()


def from_pdf(path: Path) -> str | None:
    """PDF texte via pdftotext (poppler) si dispo ; None si scan/absent."""
    exe = subprocess.run(["which", "pdftotext"], capture_output=True, text=True)
    if exe.returncode != 0:
        return None
    r = subprocess.run(["pdftotext", "-layout", str(path), "-"],
                       capture_output=True, text=True)
    txt = (r.stdout or "").strip()
    # Un PDF scanné renvoie quasi rien -> traiter comme scan.
    return txt if len(txt) > 40 else None


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: doc_to_text.py <fichier>")
    path = Path(sys.argv[1])
    if not path.exists():
        sys.exit(f"introuvable: {path}")
    ext = path.suffix.lower()

    if ext in (".docx", ".odt"):
        print(from_zip_xml(path))
    elif ext == ".txt":
        print(path.read_text(encoding="utf-8", errors="ignore").strip())
    elif ext == ".pdf":
        txt = from_pdf(path)
        if txt:
            print(txt)
        else:
            print(f"[SCAN] PDF non-texte : lire visuellement avec l'outil Read « {path} » (pages).")
    elif ext in (".jpg", ".jpeg", ".png", ".heic", ".webp"):
        print(f"[SCAN] Image : lire visuellement avec l'outil Read « {path} ».")
    else:
        print(f"[?] Format non géré ({ext}) : tenter une lecture visuelle avec Read.")


if __name__ == "__main__":
    main()
