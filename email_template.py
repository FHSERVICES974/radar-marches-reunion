#!/usr/bin/env python3
"""
email_template.py — Gabarit HTML unique pour TOUS les emails de Radar des Marchés.

Un seul style (logo, couleurs, cards) pour toutes les notifications par mail —
on alimente juste le corps avec le contenu propre à chaque type de notification
(rapport de veille, alerte, etc.), sans jamais redéfinir la charte ailleurs.

N'importe quel script Python de ce projet peut réutiliser ce module.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOGO_PATH = ROOT / "assets" / "logo_radar_marches.png"
SITE_URL = "https://radar.artisanspei.re"

# Charte graphique — identique à celle de template.html (site public)
C_BG = "#f6f4ee"
C_PANEL = "#ffffff"
C_INK = "#211f1a"
C_MUTED = "#8a8474"
C_LINE = "#e7e1d2"
C_ACCENT = "#0e6b52"   # émeraude
C_GOLD = "#a9812f"     # doré
C_ALERT = "#93453a"


def esc(s) -> str:
    return (str(s or "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def card(inner_html: str, top_border: str = C_LINE) -> str:
    """Bloc standard : fond blanc, coin arrondi, liseré de couleur en haut."""
    return (f'<div style="background:{C_PANEL};border:1px solid {C_LINE};'
            f'border-top:3px solid {top_border};border-radius:12px;'
            f'padding:20px 24px;margin-bottom:18px;">{inner_html}</div>')


def button(label: str, url: str, primary: bool = True) -> str:
    style = (f'background:{C_ACCENT};color:#fff;' if primary else
              f'background:{C_PANEL};color:{C_INK};border:1px solid {C_LINE};')
    return (f'<a href="{esc(url)}" style="display:inline-block;margin-top:14px;{style}'
            f'text-decoration:none;padding:10px 18px;border-radius:8px;'
            f'font-size:13.5px;font-weight:700;">{esc(label)}</a>')


def render_shell(subtitle: str, body_html: str, logo_cid: str | None = "logo") -> str:
    """
    Enveloppe commune : logo + nom de marque + sous-titre, puis le corps (une
    suite de card(...) déjà composée par l'appelant), puis un pied de page.
    """
    logo_block = (
        f'<img src="cid:{logo_cid}" width="64" height="64" alt="Radar des Marchés" '
        f'style="display:block;margin:0 auto 10px;border-radius:50%;">'
        if logo_cid and LOGO_PATH.exists() else ""
    )
    return f'''<!doctype html>
<html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:{C_BG};font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
<div style="max-width:600px;margin:0 auto;padding:32px 20px;">
  <div style="text-align:center;margin-bottom:24px;">
    {logo_block}
    <div style="font-size:20px;font-weight:700;color:{C_INK};letter-spacing:-.2px;">Radar des Marchés</div>
    <div style="font-size:12.5px;color:{C_MUTED};margin-top:4px;">{esc(subtitle)}</div>
  </div>
  {body_html}
  <div style="text-align:center;color:{C_MUTED};font-size:11.5px;margin-top:20px;">
    {SITE_URL}
  </div>
</div>
</body></html>'''


def attach_logo(msg, html_part_index: int = 1, cid: str = "logo") -> None:
    """À appeler après msg.set_content(plain) + msg.add_alternative(html, subtype='html')."""
    if not LOGO_PATH.exists():
        return
    html_part = msg.get_payload()[html_part_index]
    html_part.add_related(LOGO_PATH.read_bytes(), maintype="image", subtype="png", cid=cid)
