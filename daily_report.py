#!/usr/bin/env python3
"""
daily_report.py — Compose et envoie le rapport quotidien premium par mail.

Lit la proposition du jour (data/pending/pending_MAJ_AAAA-MM-JJ.json) et le
journal de la veille (veille.log), compose un mail HTML (logo, sections,
alertes, message WhatsApp prêt à copier, liens vers /admin), et l'envoie via
SMTP Gmail — Mail.app/AppleScript ne sait pas envoyer de HTML fiable.

N'écrit rien, ne publie rien : simple lecture + envoi de mail.
"""
from __future__ import annotations

import os
import smtplib
from datetime import date
from email.message import EmailMessage
from pathlib import Path

from common import ROOT, event_key, load_json, today_iso

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv(*a, **k):
        pass

load_dotenv(ROOT / ".env")

DEST_EMAIL = "shadowneox@gmail.com"
ADMIN_URL = "https://radar.artisanspei.re/admin"
LOGO_PATH = ROOT / "assets" / "logo_radar_marches.png"
URGENCE_JOURS = 10  # deadline à moins de N jours -> alerte + inclus dans le message WhatsApp


def _first_deadline(deadline_text: str):
    from common import parse_dates_from_text
    dates = parse_dates_from_text(deadline_text or "")
    return dates[0] if dates else None


def _collect_alerts(verifies: list[dict]) -> list[str]:
    alerts = []

    # 1. échecs signalés dans le journal — UNIQUEMENT le dernier passage (celui qui
    # vient de tourner), pas tout l'historique : sinon un incident déjà résolu il y
    # a des jours réapparaîtrait dans le rapport indéfiniment.
    log_path = ROOT / "veille.log"
    if log_path.exists():
        lines = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        start = 0
        for i, l in enumerate(lines):
            if l.startswith("===== VEILLE"):
                start = i
        last_run_lines = lines[start:]
        for l in last_run_lines:
            if "ATTENTION" in l:
                alerts.append(l.split("]", 1)[-1].strip() if "]" in l else l.strip())
            elif "Not logged in" in l:
                alerts.append("L'agent de veille a rencontré une erreur d'authentification pendant l'exécution — à surveiller si ça se reproduit.")

    # 2. deadlines très proches parmi les vérifiés
    today_d = date.today()
    for c in verifies:
        ev = c["event"]
        d = _first_deadline(ev.get("deadline", ""))
        if d:
            days = (d - today_d).days
            if 0 <= days <= URGENCE_JOURS:
                alerts.append(f"« {ev.get('name')} » clôture dans {days} jour(s) ({ev.get('deadline')}) — à publier vite si pertinent.")

    return alerts


def _whatsapp_message(verifies: list[dict]) -> str:
    today_d = date.today()
    urgent = []
    for c in verifies:
        ev = c["event"]
        d = _first_deadline(ev.get("deadline", ""))
        days = (d - today_d).days if d else None
        if days is not None and 0 <= days <= URGENCE_JOURS:
            urgent.append((days, ev))
    urgent.sort(key=lambda x: x[0])

    if not urgent:
        return "(Aucune deadline urgente aujourd'hui — pas de message à envoyer.)"

    lines = ["📍 *Agenda des Exposants — mise à jour*", "",
             "Nouvelles opportunités avec délais serrés :", ""]
    emojis = ["🍊", "🥔", "🎪", "🛍️", "📣"]
    for i, (days, ev) in enumerate(urgent):
        e = emojis[i % len(emojis)]
        lines.append(f"{e} *{ev.get('name')}* — {ev.get('place','?')}")
        lines.append(f"Candidature avant le *{ev.get('deadline')}* (dans {days} jour(s))")
        if ev.get("desc"):
            lines.append(ev["desc"])
        lines.append("")
    lines.append("Toutes les infos et liens de candidature : 👉 radar.artisanspei.re")
    lines.append("")
    lines.append("Le site est mis à jour chaque jour — et « Le ti artisan futé » 💬 répond à vos questions en bas de page !")
    return "\n".join(lines)


def build_report():
    today = today_iso()
    pending_path = ROOT / "data" / "pending" / f"pending_MAJ_{today}.json"
    if not pending_path.exists():
        return None

    d = load_json(pending_path, default={})
    status_changes = d.get("status_changes", [])
    candidates = d.get("new_events_candidates", [])
    community = d.get("community", [])

    published_keys = {event_key(e) for e in load_json(ROOT / "data" / "events.json", default=[])}
    candidates = [
        c for c in candidates
        if not (c.get("event") and event_key(c["event"]) in published_keys)
    ]

    verifies = [c for c in candidates if c.get("_confidence") == "Vérifié" and c.get("event")]
    probables = [c for c in candidates if c.get("_confidence") != "Vérifié" or not c.get("event")]

    alerts = _collect_alerts(verifies)
    whatsapp_msg = _whatsapp_message(verifies)

    return {
        "today": today,
        "status_changes": status_changes,
        "verifies": verifies,
        "probables": probables,
        "community": community,
        "alerts": alerts,
        "whatsapp_msg": whatsapp_msg,
    }


def _esc(s) -> str:
    return (str(s or "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def render_html(r: dict) -> str:
    C_BG, C_PANEL, C_INK, C_MUTED = "#f6f4ee", "#ffffff", "#211f1a", "#8a8474"
    C_LINE, C_ACCENT, C_GOLD, C_ALERT = "#e7e1d2", "#0e6b52", "#a9812f", "#93453a"

    def card(inner, top_border=C_LINE):
        return (f'<div style="background:{C_PANEL};border:1px solid {C_LINE};'
                f'border-top:3px solid {top_border};border-radius:12px;'
                f'padding:20px 24px;margin-bottom:18px;">{inner}</div>')

    alerts_html = ""
    if r["alerts"]:
        items = "".join(f'<li style="margin-bottom:6px;">{_esc(a)}</li>' for a in r["alerts"])
        alerts_html = card(
            f'<div style="font-weight:700;color:{C_ALERT};margin-bottom:10px;">'
            f'⚠️ Anomalies &amp; alertes</div>'
            f'<ul style="margin:0;padding-left:18px;color:{C_INK};font-size:14px;line-height:1.6;">{items}</ul>',
            top_border=C_ALERT,
        )

    def event_row(ev, extra=""):
        return (f'<div style="padding:10px 0;border-bottom:1px solid {C_LINE};">'
                f'<div style="font-weight:700;color:{C_INK};font-size:14.5px;">{_esc(ev.get("name"))}</div>'
                f'<div style="color:{C_MUTED};font-size:13px;margin-top:2px;">'
                f'{_esc(ev.get("place"))} · deadline : {_esc(ev.get("deadline") or "—")}{extra}</div></div>')

    verifies_html = ""
    if r["verifies"]:
        rows = "".join(event_row(c["event"]) for c in r["verifies"])
        verifies_html = card(
            f'<div style="font-weight:700;color:{C_ACCENT};margin-bottom:8px;">'
            f'✅ Prêts à publier ({len(r["verifies"])})</div>{rows}'
            f'<a href="{ADMIN_URL}" style="display:inline-block;margin-top:14px;background:{C_ACCENT};'
            f'color:#fff;text-decoration:none;padding:10px 18px;border-radius:8px;'
            f'font-size:13.5px;font-weight:700;">Valider dans /admin →</a>',
            top_border=C_ACCENT,
        )

    probables_html = ""
    if r["probables"]:
        rows = "".join(
            f'<div style="padding:8px 0;border-bottom:1px solid {C_LINE};font-size:13.5px;color:{C_INK};">'
            f'• {_esc((c.get("event") or {}).get("name") or c.get("_source_title"))} '
            f'<span style="color:{C_MUTED};">({_esc(c.get("_confidence"))})</span></div>'
            for c in r["probables"]
        )
        probables_html = card(
            f'<div style="font-weight:700;color:{C_GOLD};margin-bottom:8px;">'
            f'🔎 À vérifier ({len(r["probables"])})</div>{rows}'
            f'<a href="{ADMIN_URL}" style="display:inline-block;margin-top:14px;background:{C_PANEL};'
            f'color:{C_INK};text-decoration:none;padding:9px 16px;border:1px solid {C_LINE};border-radius:8px;'
            f'font-size:13px;font-weight:600;">Voir dans /admin →</a>',
            top_border=C_GOLD,
        )

    whatsapp_html = card(
        f'<div style="font-weight:700;color:{C_INK};margin-bottom:10px;">'
        f'💬 Message à copier pour le groupe WhatsApp</div>'
        f'<div style="background:{C_BG};border:1px solid {C_LINE};border-radius:8px;padding:14px 16px;'
        f'font-family:ui-monospace,Menlo,monospace;font-size:12.5px;white-space:pre-wrap;color:{C_INK};'
        f'line-height:1.55;">{_esc(r["whatsapp_msg"])}</div>'
    )

    logo_block = (
        f'<img src="cid:logo" width="64" height="64" alt="Radar des Marchés" '
        f'style="display:block;margin:0 auto 10px;border-radius:50%;">'
        if LOGO_PATH.exists() else ""
    )

    return f'''<!doctype html>
<html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:{C_BG};font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
<div style="max-width:600px;margin:0 auto;padding:32px 20px;">
  <div style="text-align:center;margin-bottom:24px;">
    {logo_block}
    <div style="font-size:20px;font-weight:700;color:{C_INK};letter-spacing:-.2px;">Radar des Marchés</div>
    <div style="font-size:12.5px;color:{C_MUTED};margin-top:4px;">Rapport de veille — {r["today"]}</div>
  </div>

  {card(
    f'<div style="font-weight:700;color:{C_INK};margin-bottom:10px;">📊 État de la veille</div>'
    f'<div style="font-size:14px;color:{C_INK};line-height:1.8;">'
    f'{len(r["verifies"])} appel(s) vérifié(s) prêt(s) à publier<br>'
    f'{len(r["probables"])} piste(s) à confirmer<br>'
    f'{len(r["status_changes"])} changement(s) de statut<br>'
    f'{len(r["community"])} remontée(s) communautaire(s)</div>'
  )}
  {alerts_html}
  {verifies_html}
  {probables_html}
  {whatsapp_html}

  <div style="text-align:center;color:{C_MUTED};font-size:11.5px;margin-top:20px;">
    Rapport généré automatiquement après la veille quotidienne (4h).
  </div>
</div>
</body></html>'''


def render_plain(r: dict) -> str:
    lines = [f'Rapport de veille — {r["today"]}', ""]
    if r["alerts"]:
        lines.append("--- Alertes ---")
        lines.extend(f"• {a}" for a in r["alerts"])
        lines.append("")
    lines.append(f'{len(r["verifies"])} vérifié(s), {len(r["probables"])} à confirmer, '
                  f'{len(r["status_changes"])} changement(s) de statut.')
    lines.append("")
    if r["verifies"]:
        lines.append("--- Prêts à publier ---")
        for c in r["verifies"]:
            ev = c["event"]
            lines.append(f"• {ev.get('name')} — {ev.get('place')} — deadline : {ev.get('deadline') or '—'}")
        lines.append("")
    if r["probables"]:
        lines.append("--- À vérifier ---")
        for c in r["probables"]:
            title = (c.get("event") or {}).get("name") or c.get("_source_title")
            lines.append(f"• {title} ({c.get('_confidence')})")
        lines.append("")
    lines.append(f"Valider : {ADMIN_URL}")
    lines.append("")
    lines.append("--- Message WhatsApp à copier ---")
    lines.append(r["whatsapp_msg"])
    return "\n".join(lines)


def send_mail(r: dict) -> None:
    sender = os.environ.get("GMAIL_SENDER", "")
    app_password = os.environ.get("GMAIL_APP_PASSWORD", "")
    if not sender or not app_password:
        raise RuntimeError(
            "GMAIL_SENDER / GMAIL_APP_PASSWORD manquants dans .env — "
            "voir .env.example pour la marche à suivre."
        )

    subject = f'Radar Marchés — {len(r["verifies"])} vérifié(s), {len(r["probables"])} à revoir'
    if r["alerts"]:
        subject = "⚠️ " + subject

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"Radar des Marchés <{sender}>"
    msg["To"] = DEST_EMAIL
    msg.set_content(render_plain(r))
    msg.add_alternative(render_html(r), subtype="html")

    if LOGO_PATH.exists():
        html_part = msg.get_payload()[1]
        html_part.add_related(
            LOGO_PATH.read_bytes(), maintype="image", subtype="png", cid="logo"
        )

    with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as server:
        server.starttls()
        server.login(sender, app_password)
        server.send_message(msg)


if __name__ == "__main__":
    report = build_report()
    if report is None:
        print("[daily_report] aucune proposition du jour — rien à envoyer.")
    else:
        send_mail(report)
        print(f"[daily_report] envoyé à {DEST_EMAIL} : "
              f"{len(report['verifies'])} vérifié(s), {len(report['probables'])} à revoir, "
              f"{len(report['alerts'])} alerte(s)")
