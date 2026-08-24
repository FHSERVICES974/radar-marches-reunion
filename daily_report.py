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

import json
import os
import re
import smtplib
import urllib.request
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
URGENCE_JOURS = 10  # deadline à moins de N jours -> alerte + inclus dans le message WhatsApp


def _first_deadline(deadline_text: str):
    """Date de clôture réelle d'un texte de deadline.

    ⚠️ Ne jamais reprendre `dates[0]` ici. `parse_dates_from_text` reconnaît aussi
    le motif « mois année » et fabrique donc une date parasite au 1er du mois :

        "19 août 2026, 14h00"  ->  [2026-08-01, 2026-08-19]

    Prendre la première renvoyait le 1er août — soit une deadline systématiquement
    dans le passé, et donc aucune alerte ne partait jamais (bug silencieux corrigé
    le 15 août 2026, découvert sur l'AOT de Saint-Gilles calculée à J-14 au lieu
    de J+4). La date parasite étant toujours le 1er du mois, elle est toujours la
    plus petite : `max()` retient la vraie échéance.
    """
    from common import parse_dates_from_text
    dates = parse_dates_from_text(deadline_text or "")
    return max(dates) if dates else None


# Statuts d'events.json qui correspondent à une candidature réellement ouverte.
STATUTS_OUVERTS = {"open", "soon"}

# Garde-fou sur le champ `deadline`, qui est affiché tel quel dans le rapport :
# au-delà de cette longueur ET avec un marqueur de procédure, c'est que le mode
# d'emploi a débordé dans un champ prévu pour une date.
DEADLINE_MAX_CAR = 40

# Horizon de la liste « sans date exploitable » : au-delà, ce n'est pas encore
# actionnable et ça encombrerait le rapport.
MOIS_SANS_DATE = 3
_DEADLINE_PROCEDURE = re.compile(
    r"€|formulaire|paiement|pré-?inscription|inscription via|lien en bio|à régler", re.I)


def _urgent_published(today_d) -> list[tuple[int, dict]]:
    """Événements DÉJÀ PUBLIÉS dont la deadline tombe dans les N prochains jours.

    Le rapport ne regardait que les captures de la nuit. Un appel publié il y a
    trois jours et qui ferme demain n'apparaissait donc nulle part — c'est
    justement le cas le plus urgent pour la communauté.
    """
    urgents = []
    for ev in load_json(ROOT / "data" / "events.json", default=[]):
        if ev.get("status") not in STATUTS_OUVERTS:
            continue
        d = _first_deadline(ev.get("deadline", ""))
        if not d:
            continue
        days = (d - today_d).days
        if 0 <= days <= URGENCE_JOURS:
            urgents.append((days, ev))
    urgents.sort(key=lambda x: x[0])
    return urgents


def _noms_sous_surveillance(mois: int) -> set:
    """Noms d'événements dont une fenêtre de veille saisonnière est ouverte ce mois-ci.

    Fait le lien avec data/veille_calendrier.json : une fiche sans date dont l'appel
    est réputé s'ouvrir MAINTENANT est actionnable, même si l'événement est loin.
    """
    cal = load_json(ROOT / "data" / "veille_calendrier.json", default={})
    return {s.get("nom", "") for s in cal.get("surveillances", [])
            if mois in (s.get("mois_de_veille") or [])}


def _sans_date_exploitable(today_d) -> list[dict]:
    """Fiches ouvertes dont la clôture n'est PAS analysable, événement proche.

    Le trou de fond du rapport : toutes les listes d'urgence reposent sur
    `_first_deadline()`. Une fiche dont le `deadline` est vide ou non daté
    (« Appel à candidature été/automne », « Appel à forains 2026 à surveiller »)
    n'apparaît donc dans AUCUNE liste — ni en retard, ni du tout. Son silence est
    indiscernable d'un « rien à signaler ».

    C'est exactement ce qu'étaient Florilèges et la Fête de l'ail, ratés le 14/08/2026,
    et les neuf marchés de Noël sans deadline connue.

    Volontairement HORS du message WhatsApp : c'est une liste de travail pour
    François, pas une alerte pour les artisans.
    """
    manquantes = []
    for ev in load_json(ROOT / "data" / "events.json", default=[]):
        if ev.get("status") not in STATUTS_OUVERTS:
            continue
        if _first_deadline(ev.get("deadline", "")):
            continue                      # une date exploitable : déjà couverte ailleurs
        m = ev.get("month")
        if not isinstance(m, int) or not 1 <= m <= 12:
            continue                      # 99 = variable/permanent : pas d'échéance à rater
        dans = (m - today_d.month) % 12   # nombre de mois avant l'événement
        # Deux raisons d'être dans la liste. La distance à l'événement ne suffit
        # pas : un marché de Noël est à 4 mois en août, donc hors fenêtre — alors
        # que son APPEL s'ouvre précisément en août. On retient donc aussi les
        # fiches dont une surveillance saisonnière est ouverte ce mois-ci.
        if dans <= MOIS_SANS_DATE or ev.get("name", "") in _noms_sous_surveillance(today_d.month):
            manquantes.append({"nom": ev.get("name", ""), "mois": dans,
                               "url": ev.get("url", ""), "contact": ev.get("contact", ""),
                               "deadline_brut": (ev.get("deadline") or "").strip()})
    manquantes.sort(key=lambda x: (x["mois"], x["nom"]))
    return manquantes



SITE_URL = "https://radar.artisanspei.re/"
SITE_TIMEOUT = 15


def _divergence_prod() -> str | None:
    """Le site en ligne dit-il la même chose que le dépôt ?

    Le 24/08/2026, la production a servi pendant ~30 h des données absentes du
    dépôt : le jeton GitHub avait expiré, les écritures réussissaient sur le
    disque de la VM mais aucun push ne partait. Rien ne le signalait. Un
    redéploiement aurait effacé ces données sans prévenir.

    On compare ce qui est lisible par un humain — date de mise à jour et nombre
    de fiches — plutôt que des empreintes opaques : le message doit dire quoi
    faire, pas seulement qu'il y a un écart.

    Retourne None si tout concorde, sinon la phrase d'alerte.
    """
    try:
        req = urllib.request.Request(SITE_URL, headers={"Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=SITE_TIMEOUT) as r:
            page = r.read().decode("utf-8", errors="replace")
    except Exception as exc:
        # Site injoignable : c'est en soi une alerte, et une plus grave.
        return (f"SITE INJOIGNABLE — {SITE_URL} n'a pas répondu ({type(exc).__name__}). "
                f"Vérifier que le déploiement Replit tourne.")

    m_meta = re.search(r'META\s*=\s*\{[^}]*lastUpdate:\s*"([^"]+)"', page)
    m_ev   = re.search(r"EVENTS\s*=\s*(\[.*?\])\s*;", page, re.S)
    if not m_meta or not m_ev:
        return ("PAGE ILLISIBLE — la page en ligne ne contient plus ni META ni EVENTS "
                "exploitables. Le build a peut-être produit une page cassée.")
    try:
        en_ligne_n = len(json.loads(m_ev.group(1)))
    except json.JSONDecodeError:
        return "PAGE ILLISIBLE — le tableau EVENTS de la page en ligne n'est pas du JSON valide."
    en_ligne_maj = m_meta.group(1)

    depot_maj = (load_json(ROOT / "data" / "meta.json", default={}) or {}).get("lastUpdate", "")
    depot_n   = len(load_json(ROOT / "data" / "events.json", default=[]))

    if en_ligne_maj == depot_maj and en_ligne_n == depot_n:
        return None

    ecarts = []
    if en_ligne_maj != depot_maj:
        ecarts.append(f"mise à jour {en_ligne_maj} en ligne contre {depot_maj} dans le dépôt")
    if en_ligne_n != depot_n:
        ecarts.append(f"{en_ligne_n} fiches en ligne contre {depot_n} dans le dépôt")
    return ("DIVERGENCE — le site en ligne ne correspond pas au dépôt : " + " ; " .join(ecarts)
            + ". Soit une publication n'est pas partie (jeton GitHub ?), soit le déploiement "
              "Replit n'a pas été relancé. ⚠️ Ne pas redéployer avant d'avoir vérifié : "
              "un redéploiement écrase le disque de production depuis GitHub.")


def _collect_alerts(verifies: list[dict]) -> list[str]:
    alerts = []

    # 0. cohérence site en ligne / dépôt — d'abord, car elle conditionne le reste
    div = _divergence_prod()
    if div:
        alerts.append(div)

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

    # 3. deadlines très proches parmi les événements DÉJÀ EN LIGNE
    for days, ev in _urgent_published(today_d):
        quand = "aujourd'hui" if days == 0 else ("demain" if days == 1 else f"dans {days} jours")
        alerts.append(f"EN LIGNE — « {ev.get('name')} » clôture {quand} ({ev.get('deadline')}).")

    # 4. qualité du champ `deadline` : il est AFFICHÉ TEL QUEL dans ce rapport.
    # Cas réel (Maison Banian, 16/08/2026) : toute la procédure avait été écrite
    # dans `deadline`, d'où un mail annonçant « clôture le Pré-inscription via
    # formulaire, sélection puis paiement (210€ TTC…) ».
    #
    # On ne teste PAS « longue et sans date extractible » : ça signalerait onze
    # fiches parfaitement légitimes (« Éd. 2026 clôturée — surveiller 2027 »,
    # « Non précisée sur la page »), et le bruit ferait cesser de lire les alertes.
    # Le signal fiable, c'est la présence d'un marqueur de PROCÉDURE : un prix, un
    # formulaire, un paiement. Ces mots-là n'ont rien à faire dans une date.
    for ev in load_json(ROOT / "data" / "events.json", default=[]):
        dl = (ev.get("deadline") or "").strip()
        if len(dl) > DEADLINE_MAX_CAR and _DEADLINE_PROCEDURE.search(dl):
            alerts.append(
                f"DONNÉE — « {ev.get('name')} » : le champ `deadline` décrit une procédure "
                f"({len(dl)} caractères) au lieu de porter une date. Il s'affiche tel quel "
                f"dans ce rapport. Garder la date seule dans `deadline`, le reste dans `apply`.")

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

    # Les appels déjà publiés qui ferment bientôt comptent autant que les
    # nouveautés : ce sont eux que la communauté risque de laisser passer.
    # (`verifies` est déjà dédoublonné contre events.json dans build_report.)
    deja_en_ligne = _urgent_published(today_d)

    if not urgent and not deja_en_ligne:
        return ("(Aucune deadline dans les 10 jours, ni parmi les nouveautés de la nuit, "
                "ni parmi les appels déjà en ligne — pas de message à envoyer.)")

    lines = ["📍 *Agenda des Exposants — mise à jour*", ""]
    emojis = ["🍊", "🥔", "🎪", "🛍️", "📣"]

    def bloc(items, titre, decalage=0):
        if not items:
            return
        lines.append(titre)
        lines.append("")
        for i, (days, ev) in enumerate(items):
            e = emojis[(i + decalage) % len(emojis)]
            quand = "aujourd'hui" if days == 0 else ("demain" if days == 1 else f"dans {days} jours")
            lines.append(f"{e} *{ev.get('name')}* — {ev.get('place','?')}")
            lines.append(f"Candidature avant le *{ev.get('deadline')}* ({quand})")
            if ev.get("desc"):
                lines.append(ev["desc"])
            lines.append("")

    bloc(urgent, "Nouvelles opportunités avec délais serrés :")
    bloc(deja_en_ligne, "⏰ Ça ferme bientôt, déjà sur le radar :", decalage=len(urgent))

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
    sans_date = _sans_date_exploitable(date.today())
    whatsapp_msg = _whatsapp_message(verifies)

    return {
        "today": today,
        "status_changes": status_changes,
        "verifies": verifies,
        "probables": probables,
        "community": community,
        "alerts": alerts,
        "sans_date": sans_date,   # liste de travail, JAMAIS dans le WhatsApp
        "whatsapp_msg": whatsapp_msg,
    }


import email_template as tpl

_esc = tpl.esc
card = tpl.card
C_BG, C_PANEL, C_INK, C_MUTED = tpl.C_BG, tpl.C_PANEL, tpl.C_INK, tpl.C_MUTED
C_LINE, C_ACCENT, C_GOLD, C_ALERT = tpl.C_LINE, tpl.C_ACCENT, tpl.C_GOLD, tpl.C_ALERT


def render_html(r: dict) -> str:
    alerts_html = ""
    if r["alerts"]:
        items = "".join(f'<li style="margin-bottom:6px;">{_esc(a)}</li>' for a in r["alerts"])
        alerts_html = card(
            f'<div style="font-weight:700;color:{C_ALERT};margin-bottom:10px;">'
            f'⚠️ Anomalies &amp; alertes</div>'
            f'<ul style="margin:0;padding-left:18px;color:{C_INK};font-size:14px;line-height:1.6;">{items}</ul>',
            top_border=C_ALERT,
        )

    # Bloc SÉPARÉ des alertes : ce n'est pas une urgence datée, c'est un angle mort.
    # Ces fiches n'apparaissent dans aucune autre liste, faute de date analysable.
    sans_date_html = ""
    if r.get("sans_date"):
        items = "".join(
            f'<li style="margin-bottom:6px;">{_esc(x["nom"])}'
            + (f' — <span style="color:{C_MUTED};">événement '
               + ("ce mois-ci" if x["mois"] == 0 else f'dans {x["mois"]} mois') + '</span>')
            + (f' · <a href="{_esc(x["url"])}" style="color:{C_ACCENT};">source officielle</a>'
               if x["url"] else
               (f' · <span style="color:{C_MUTED};">{_esc(x["contact"])}</span>' if x["contact"] else ''))
            + '</li>'
            for x in r["sans_date"])
        sans_date_html = card(
            f'<div style="font-weight:700;color:{C_GOLD};margin-bottom:4px;">'
            f'🕳️ Sans date de clôture exploitable ({len(r["sans_date"])})</div>'
            f'<div style="color:{C_MUTED};font-size:13px;margin-bottom:10px;">'
            f'Événement sous {MOIS_SANS_DATE} mois, mais aucune date de clôture analysable : '
            f'ces fiches n\'apparaissent dans <b>aucune</b> liste d\'urgence. '
            f'Leur silence n\'est pas un « rien à signaler ». À vérifier à la source.</div>'
            f'<ul style="margin:0;padding-left:18px;color:{C_INK};font-size:14px;line-height:1.6;">{items}</ul>',
            top_border=C_GOLD,
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
            f'{tpl.button("Valider dans /admin →", ADMIN_URL, primary=True)}',
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
            f'{tpl.button("Voir dans /admin →", ADMIN_URL, primary=False)}',
            top_border=C_GOLD,
        )

    whatsapp_html = card(
        f'<div style="font-weight:700;color:{C_INK};margin-bottom:10px;">'
        f'💬 Message à copier pour le groupe WhatsApp</div>'
        f'<div style="background:{C_BG};border:1px solid {C_LINE};border-radius:8px;padding:14px 16px;'
        f'font-family:ui-monospace,Menlo,monospace;font-size:12.5px;white-space:pre-wrap;color:{C_INK};'
        f'line-height:1.55;">{_esc(r["whatsapp_msg"])}</div>'
    )

    etat_html = card(
        f'<div style="font-weight:700;color:{C_INK};margin-bottom:10px;">📊 État de la veille</div>'
        f'<div style="font-size:14px;color:{C_INK};line-height:1.8;">'
        f'{len(r["verifies"])} appel(s) vérifié(s) prêt(s) à publier<br>'
        f'{len(r["probables"])} piste(s) à confirmer<br>'
        f'{len(r["status_changes"])} changement(s) de statut<br>'
        f'{len(r["community"])} remontée(s) communautaire(s)</div>'
    )

    body = etat_html + alerts_html + sans_date_html + verifies_html + probables_html + whatsapp_html + (
        f'<div style="text-align:center;color:{C_MUTED};font-size:11.5px;margin-top:4px;">'
        f'Rapport généré automatiquement après la veille quotidienne (4h).</div>'
    )
    return tpl.render_shell(subtitle=f'Rapport de veille — {r["today"]}', body_html=body)


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
    tpl.attach_logo(msg)

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
