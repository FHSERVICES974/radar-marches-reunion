#!/usr/bin/env python3
"""
Serveur statique + webhook GitHub.
Sert index.html sur le port 5000 et expose /sync pour déclencher
un git pull automatique à chaque push sur la branche main.
"""

import base64
import datetime
import hashlib
import hmac
import html
import http.server
import json
import logging
import os
import smtplib
import subprocess
import threading
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from email.message import EmailMessage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

WEBHOOK_SECRET = os.environ.get("GITHUB_WEBHOOK_SECRET", "").encode()
BRANCH = "main"

# Intervalle (secondes) entre chaque WARNING périodique quand git est absent
_GIT_WARN_INTERVAL = 300  # 5 minutes


def _git_available() -> bool:
    """Retourne True si un dépôt git est présent dans le répertoire courant."""
    return os.path.isdir(".git")


def _periodic_git_warning():
    """Thread daemon : émet un WARNING toutes les _GIT_WARN_INTERVAL secondes
    tant que le dépôt git n'est pas initialisé."""
    while not _git_available():
        log.warning(
            "ALERTE MODE DÉGRADÉ : aucun dépôt git détecté. "
            "Le endpoint /sync est indisponible. "
            "Redémarrez le serveur avec scripts/start.sh pour rétablir l'accès à GitHub."
        )
        time.sleep(_GIT_WARN_INTERVAL)


# ---------------------------------------------------------------------------
# Notification d'alerte mode dégradé
# Variables d'environnement supportées :
#   ALERT_WEBHOOK_URL   — URL webhook (Slack / Discord / générique)
#   ALERT_EMAIL         — adresse email destinataire
#   SMTP_HOST           — serveur SMTP (défaut : localhost)
#   SMTP_PORT           — port SMTP    (défaut : 587)
#   SMTP_USER           — identifiant SMTP (optionnel)
#   SMTP_PASSWORD       — mot de passe SMTP (optionnel)
#   SMTP_FROM           — expéditeur   (défaut : noreply@localhost)
# ---------------------------------------------------------------------------

_ALERT_MESSAGE = (
    "⚠️ ALERTE MODE DÉGRADÉ\n\n"
    "Le serveur a démarré SANS dépôt git.\n"
    "Le endpoint /sync (synchronisation GitHub) est indisponible.\n\n"
    "Action requise : redémarrez le serveur via scripts/start.sh pour rétablir la synchronisation.\n"
    "Vérifiez GET /health pour surveiller l'état du service."
)


def _send_webhook_alert(url: str) -> bool:
    """Envoie une alerte via webhook (Slack / Discord / URL générique).
    Retourne True en cas de succès."""
    # Format compatible Slack et Discord (champ "text")
    payload = json.dumps({"text": _ALERT_MESSAGE}).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            log.info("Alerte webhook envoyée (HTTP %d).", resp.status)
            return True
    except urllib.error.URLError as exc:
        log.error("Échec envoi alerte webhook : %s", exc)
        return False


def _send_email(subject: str, body: str, recipient: str) -> bool:
    """Envoie un email via SMTP (mécanisme unique de l'app).
    Retourne True en cas de succès."""
    smtp_host = os.environ.get("SMTP_HOST", "localhost")
    smtp_port = int(os.environ.get("SMTP_PORT", 587))
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_password = os.environ.get("SMTP_PASSWORD", "")
    smtp_from = os.environ.get("SMTP_FROM", "noreply@localhost")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = smtp_from
    msg["To"] = recipient
    msg.set_content(body)

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as server:
            server.ehlo()
            if smtp_port != 25:
                server.starttls()
                server.ehlo()
            if smtp_user and smtp_password:
                server.login(smtp_user, smtp_password)
            server.send_message(msg)
        log.info("Email envoyé à %s (%s).", recipient, subject[:60])
        return True
    except Exception as exc:
        log.error("Échec envoi email : %s", exc)
        return False


def _send_email_alert(recipient: str) -> bool:
    """Alerte de démarrage en mode dégradé (compat historique)."""
    return _send_email("⚠️ Serveur démarré en mode dégradé (git absent)",
                       _ALERT_MESSAGE, recipient)


def _send_degraded_alert():
    """Envoie une notification unique lors d'un démarrage en mode dégradé.
    Tente le webhook en priorité, puis l'email si configuré."""
    webhook_url = os.environ.get("ALERT_WEBHOOK_URL", "").strip()
    alert_email = os.environ.get("ALERT_EMAIL", "").strip()

    if not webhook_url and not alert_email:
        log.info(
            "Aucune notification d'alerte configurée. "
            "Définissez ALERT_WEBHOOK_URL ou ALERT_EMAIL pour recevoir une alerte au démarrage dégradé."
        )
        return

    if webhook_url:
        _send_webhook_alert(webhook_url)

    if alert_email:
        _send_email_alert(alert_email)


def verify_signature(payload: bytes, signature_header: str) -> bool:
    """Vérifie la signature HMAC-SHA256 envoyée par GitHub."""
    if not WEBHOOK_SECRET:
        log.warning("GITHUB_WEBHOOK_SECRET non défini — vérification désactivée.")
        return True
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(WEBHOOK_SECRET, payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


# Sérialise toutes les opérations git (webhook /sync, publication admin,
# push des décisions) pour éviter les courses fetch/reset/commit/push.
_git_ops_lock = threading.Lock()


def git_pull():
    """Synchronise le working tree avec origin/main.

    Utilise git fetch + git reset --hard FETCH_HEAD plutôt que git pull,
    pour éviter l'erreur "untracked files would be overwritten by merge"
    qui survient quand le bundle de déploiement contient des fichiers
    non-trackés par git (pipeline scripts, data/, etc.).
    git reset --hard force le working tree à correspondre au remote
    sans se bloquer sur les fichiers non-trackés.
    """
    import os as _os

    if not _os.path.isdir(".git"):
        log.error(
            "ERREUR CRITIQUE : Pas de dépôt git dans ce conteneur. "
            "Le déploiement doit utiliser scripts/start.sh pour initialiser git. "
            "Vérifiez que la cible de déploiement est 'vm' dans .replit."
        )
        return

    try:
        with _git_ops_lock:
            # Étape 1 : fetch
            fetch = subprocess.run(
                ["git", "fetch", "origin", BRANCH, "--depth=1"],
                capture_output=True, text=True, timeout=60,
            )
            if fetch.returncode != 0:
                log.error("git fetch a échoué (code %d) :\n%s",
                          fetch.returncode, fetch.stderr.strip())
                return

            # Étape 2 : reset hard — force le working tree sans se bloquer
            # sur les fichiers non-trackés présents dans le bundle de déploiement
            reset = subprocess.run(
                ["git", "reset", "--hard", "FETCH_HEAD"],
                capture_output=True, text=True, timeout=30,
            )
            if reset.returncode == 0:
                log.info("Sync réussie (fetch + reset) :\n%s", reset.stdout.strip())
                _invalidate_events_cache()
            else:
                log.error("git reset --hard a échoué (code %d) :\n%s",
                          reset.returncode, reset.stderr.strip())
    except subprocess.TimeoutExpired:
        log.error("git fetch/reset a expiré après 60 secondes.")
    except Exception as exc:
        log.error("Erreur inattendue lors de la sync git : %s", exc)


# ── Le ti artisan futé — Assistant IA ─────────────────────────────────────

_ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# ── Modèles Claude — référence de configuration ────────────────────────────
# Pour changer de génération manuellement : modifiez ces deux lignes seulement.
# Le thread _model_check_loop surveille que ces modèles restent actifs et
# bascule automatiquement vers un remplaçant du même tier si l'un est retiré.
_MODEL_FAST   = "claude-haiku-4-5-20251001"   # rapide/économique — marchés du site
_MODEL_STRONG = "claude-sonnet-4-5-20250929"  # plus fort — questions admin/recherche

# Mots-clés qui identifient chaque tier de coût.
# Un modèle FAST ne peut remplacer que du FAST, STRONG que du STRONG.
_TIER_KEYWORDS: dict = {
    "FAST":   ["haiku"],
    "STRONG": ["sonnet"],
}

# Noms actifs courants — initialisés depuis les constantes, puis maintenus à jour
# par _check_models_once(). Accès protégé par _models_lock.
_active_models: dict  = {"FAST": _MODEL_FAST, "STRONG": _MODEL_STRONG}
_models_lock          = threading.Lock()
_MODEL_CHECK_INTERVAL = 24 * 3600  # vérification quotidienne


def _get_model(tier: str) -> str:
    """Retourne le nom du modèle actif pour le tier donné ('FAST' ou 'STRONG')."""
    with _models_lock:
        return _active_models[tier]


def _model_tier(model_id: str) -> str | None:
    """Classe un modèle dans son tier d'après son nom, ou None si inconnu."""
    for tier, keywords in _TIER_KEYWORDS.items():
        if any(kw in model_id for kw in keywords):
            return tier
    return None


def _fetch_model_ids() -> list:
    """Retourne la liste ordonnée des IDs de modèles actifs depuis l'API Anthropic."""
    if not _ANTHROPIC_API_KEY:
        return []
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/models",
        headers={
            "x-api-key":         _ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return [m["id"] for m in json.loads(resp.read()).get("data", [])]
    except Exception as exc:
        log.warning("Vérification modèles Anthropic : API inaccessible (%s). Modèles actuels conservés.", exc)
        return []


def _check_models_once() -> None:
    """Vérifie que les modèles actifs sont toujours disponibles ; bascule si nécessaire."""
    ids = _fetch_model_ids()
    if not ids:
        return  # avertissement déjà loggé dans _fetch_model_ids
    active_set = set(ids)
    with _models_lock:
        for tier in ("FAST", "STRONG"):
            current = _active_models[tier]
            if current in active_set:
                log.info("Modèle %s ('%s') : actif.", tier, current)
                continue
            # Modèle absent — chercher le meilleur du même tier (l'API renvoie du plus récent au plus ancien)
            candidates = [m for m in ids if _model_tier(m) == tier]
            if candidates:
                replacement = candidates[0]
                _active_models[tier] = replacement
                log.warning(
                    "MODÈLE REMPLACÉ [tier %s] : '%s' n'est plus disponible. "
                    "Basculement automatique vers '%s'. "
                    "Mettez à jour la constante _MODEL_%s dans server.py.",
                    tier, current, replacement, tier,
                )
            else:
                log.error(
                    "ALERTE MODÈLE [tier %s] : '%s' n'est plus disponible "
                    "et aucun remplaçant de même niveau n'a été trouvé. "
                    "Les appels Claude vont échouer. Mettez à jour manuellement server.py.",
                    tier, current,
                )


def _model_check_loop() -> None:
    """Thread daemon : vérifie les modèles au démarrage puis toutes les 24 h."""
    _check_models_once()
    while True:
        time.sleep(_MODEL_CHECK_INTERVAL)
        _check_models_once()


# ─────────────────────────────────────────────────────────────────────────

# Limitation : 20 messages par heure et par IP
_RATE_MAX    = 20
_RATE_WINDOW = 3600  # secondes

_rate_store: dict = {}
_rate_lock  = threading.Lock()


def _check_rate(ip: str) -> bool:
    now = time.time()
    with _rate_lock:
        ts = [t for t in _rate_store.get(ip, []) if now - t < _RATE_WINDOW]
        if len(ts) >= _RATE_MAX:
            _rate_store[ip] = ts
            return False
        ts.append(now)
        _rate_store[ip] = ts
        return True


# Cache du résumé des événements (invalidé après chaque git pull)
_events_cache: list = [None]
_events_lock  = threading.Lock()


def _load_events() -> str:
    """Lit index.html, parse le tableau EVENTS (JSON) et retourne un résumé lisible."""
    with _events_lock:
        if _events_cache[0] is not None:
            return _events_cache[0]
        try:
            with open("index.html", encoding="utf-8") as f:
                html = f.read()
            m = re.search(r"const EVENTS = (\[.+?\]);", html, re.DOTALL)
            if not m:
                log.warning("_load_events : tableau EVENTS introuvable dans index.html.")
                _events_cache[0] = ""
                return ""
            events = json.loads(m.group(1))
            _STATUS_LABELS = {
                "open":   "Candidature ouverte",
                "soon":   "À surveiller / appel à venir",
                "closed": "Clôturée",
                "perm":   "Marché permanent",
            }
            lines = []
            for ev in events:
                row = [f"▸ {ev.get('name', '?')}"]
                if ev.get("zone"):     row.append(f"  Zone : {ev['zone']}")
                if ev.get("type"):     row.append(f"  Type : {ev['type']}")
                if ev.get("place"):    row.append(f"  Lieu : {ev['place']}")
                if ev.get("when"):     row.append(f"  Quand : {ev['when']}")
                status = ev.get("status", "")
                if status:             row.append(f"  Statut : {_STATUS_LABELS.get(status, status)}")
                if ev.get("deadline"): row.append(f"  Délai candidature : {ev['deadline']}")
                if ev.get("apply"):    row.append(f"  Comment candidater : {ev['apply']}")
                if ev.get("contact"):  row.append(f"  Contact : {ev['contact']}")
                lines.append("\n".join(row))
            _events_cache[0] = "\n\n".join(lines)
            log.info("Cache événements chargé (%d événements).", len(events))
        except Exception as exc:
            log.error("Erreur chargement événements : %s", exc)
            _events_cache[0] = ""
        return _events_cache[0]


def _invalidate_events_cache() -> None:
    with _events_lock:
        _events_cache[0] = None
    log.info("Cache événements invalidé.")


# Mots-clés pour détecter une question sur les événements du site
_EVT_KW = {
    "marché", "marche", "salon", "foire", "candidat", "exposant", "appel",
    "date", "délai", "deadline", "inscri", "dossier", "stand", "emplacement",
    "quand", "agenda", "organisateur", "nord", "sud", "est", "ouest",
    "saint-denis", "saint-paul", "saint-pierre", "bras-panon", "permanent",
    "mensuel", "hebdo", "trimestr", "annuel", "noël", "dipavali",
    "contact", "zone", "événement", "evenement", "répertoire", "repertoire",
    "liste", "calendrier", "prochaine", "prochain",
}


def _is_events_q(text: str) -> bool:
    tl = text.lower()
    return any(kw in tl for kw in _EVT_KW)


_SYS_EVENTS = (
    "Tu es « Le ti artisan futé », l'assistant chaleureux du site Agenda des Exposants — Artisans de La Réunion.\n"
    "Tu parles avec bienveillance, de façon simple et amicale, comme si tu aidais un ami artisan. Pas de jargon, "
    "jamais condescendant, toujours encourageant.\n\n"
    "Ta mission : répondre aux questions sur les marchés, foires, salons et appels à candidatures listés sur le site.\n"
    "Les données ci-dessous sont ta seule source de vérité pour les dates, délais, modalités et contacts.\n"
    "Si une information n'est pas dans les données, dis-le honnêtement et oriente vers l'organisateur ou la mairie.\n\n"
    "FORMAT STRICT : texte brut uniquement. Zéro astérisque, zéro dièse, zéro tiret long (—). "
    "Pour les listes, commence chaque élément par un tiret simple «- ». "
    "Sépare les idées par des retours à la ligne. 3–4 paragraphes courts maximum.\n\n"
    "LISTE DES ÉVÉNEMENTS DU SITE :\n{events}"
)

_SYS_ADMIN = (
    "Tu es « Le ti artisan futé », un assistant chaleureux qui aide les artisans et créateurs de La Réunion "
    "avec leurs démarches.\n"
    "Tu parles avec bienveillance, de façon simple et amicale. Pas de jargon, jamais condescendant, "
    "toujours encourageant.\n\n"
    "Pour les questions administratives (statut, immatriculation, cotisations, impôts, aides locales…), "
    "tu peux t'appuyer sur les informations disponibles sur ces sites officiels uniquement :\n"
    "• artisanat974.re et cma-reunion.fr (Chambre de Métiers et de l'Artisanat de La Réunion)\n"
    "• service-public.fr et entreprendre.service-public.fr (démarches nationales)\n"
    "• urssaf.fr (cotisations sociales)\n"
    "• impots.gouv.fr (fiscalité)\n"
    "• regionreunion.com (aides et subventions de la Région Réunion)\n"
    "• departement974.fr (aides et démarches du Département de La Réunion)\n\n"
    "RÈGLE ABSOLUE : Pour tout chiffre précis (taux, seuils, montants, plafonds…), indique toujours "
    "qu'il faut vérifier avec la Chambre de Métiers de La Réunion ou un comptable, car ces données "
    "changent régulièrement. Ne jamais affirmer un chiffre avec certitude.\n"
    "Si la question sort de ces domaines, oriente poliment vers la CMA ou un professionnel.\n\n"
    "FORMAT STRICT : texte brut uniquement. Zéro astérisque, zéro dièse, zéro tiret long (—). "
    "Pour les listes, commence chaque élément par un tiret simple «- ». "
    "Sépare les idées par des retours à la ligne. 3–4 paragraphes courts maximum."
)


def _claude(model: str, system: str, messages: list) -> str:
    """Appel à l'API Anthropic Claude."""
    if not _ANTHROPIC_API_KEY:
        return "Désolé, le service est momentanément indisponible."
    payload = json.dumps({
        "model": model,
        "max_tokens": 600,
        "system": system,
        "messages": messages,
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "content-type":      "application/json",
            "x-api-key":         _ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            return data["content"][0]["text"].strip()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        log.error("Anthropic HTTP %d : %s", exc.code, body[:300])
        return "Désolé, une erreur est survenue. Réessaie dans quelques instants 🙏"
    except Exception as exc:
        log.error("Anthropic error : %s", exc)
        return "Désolé, une erreur est survenue. Réessaie dans quelques instants 🙏"


# ── Vérification/complétion IA d'un candidat (admin) ────────────────────────

_VERIFY_SYS = (
    "Tu es un agent de vérification pour un agenda d'appels à candidature "
    "destiné aux artisans/créateurs exposants de La Réunion (974). "
    "Ta mission : vérifier et compléter la fiche d'UN candidat d'événement, "
    "avec une discipline stricte :\n"
    "1. Ne JAMAIS inventer une date, un lieu ou un contact. Chaque information "
    "doit être confirmée par une source réellement lue (page fournie ou "
    "résultat de recherche web que tu as consulté).\n"
    "2. Exige un signal La Réunion (974) clair : domaine .re, mention "
    "« Réunion / 974 / La Réunion », ou commune réunionnaise identifiable. "
    "Attention aux homonymes métropole (Saint-Denis 93, Saint-Paul 60…).\n"
    "3. La fiche n'est complète que si elle a une URL source réelle que tu as "
    "effectivement consultée. Sinon, elle reste incomplète.\n"
    "4. Mieux vaut une fiche incomplète honnête qu'une fiche complète douteuse.\n\n"
    "La fiche complète comporte EXACTEMENT ces 16 champs :\n"
    "name, zone (Nord/Est/Ouest/Sud/National), type, org (organisateur), "
    "place, when (période lisible), badge (court en majuscules ex. OCT), "
    "month (1-12, ou 99 si variable), dateStatus (confirmée/annuel/récurrent/"
    "probable…), status (open/soon/closed/perm), deadline, contact, social, "
    "url, apply (comment candidater), desc (description courte).\n\n"
    "Réponds UNIQUEMENT avec un objet JSON (aucun texte autour) :\n"
    '{"complete": true|false, "event": {…16 champs…} ou null, '
    '"report": "ce qui a été trouvé/vérifié, et ce qui manque ou reste non '
    'confirmé (en français, 2-5 phrases)"}\n'
    "Mets complete=true SEULEMENT si les 16 champs sont remplis à partir de "
    "sources vérifiées. Les champs social/deadline/contact peuvent être vides "
    "(\"\") s'ils n'existent pas publiquement, mais name, zone, place, when, "
    "url et desc doivent être confirmés."
)


def _fetch_page_text(url: str) -> str:
    """Télécharge une page publique et retourne son texte brut (HTML dépouillé).

    Refuse les cibles internes (anti-SSRF) : localhost, IPs privées/link-local.
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("URL non supportée")
    import ipaddress
    import socket
    for res in socket.getaddrinfo(parsed.hostname, None):
        ip = ipaddress.ip_address(res[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ValueError("adresse interne refusée")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (RadarAdmin/1.0)"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read(300_000).decode("utf-8", errors="replace")
    raw = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    raw = re.sub(r"\s+", " ", raw)
    return raw[:15_000]


def _verify_candidate_with_ai(candidate: dict, info: str) -> tuple:
    """Vérifie/complète un candidat via Claude + recherche web.

    Retourne (complete: bool, event: dict|None, report: str).
    """
    if not _ANTHROPIC_API_KEY:
        return False, None, "Service IA indisponible (clé API manquante)."

    # Récupérer le contenu des URLs collées par le propriétaire.
    pages = []
    for url in re.findall(r"https?://\S+", info)[:3]:
        try:
            pages.append(f"--- Contenu de {url} ---\n{_fetch_page_text(url)}")
        except Exception as exc:
            pages.append(f"--- {url} : téléchargement impossible ({exc}) ---")

    user_msg = (
        "Candidat actuel (JSON) :\n"
        + json.dumps(candidate, ensure_ascii=False, indent=1)
        + "\n\nInformations fournies par le propriétaire :\n" + (info or "(aucune)")
        + ("\n\n" + "\n\n".join(pages) if pages else "")
        + "\n\nVérifie et complète la fiche. Utilise la recherche web si besoin. "
          "Réponds uniquement avec le JSON demandé."
    )
    payload = json.dumps({
        "model": _get_model("STRONG"),
        "max_tokens": 3000,
        "system": _VERIFY_SYS,
        "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}],
        "messages": [{"role": "user", "content": user_msg}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "content-type":      "application/json",
            "x-api-key":         _ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=240) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        log.error("Vérification IA — Anthropic HTTP %d : %s", exc.code, body[:300])
        return False, None, f"Erreur API Anthropic (HTTP {exc.code}). Réessayez."
    except Exception as exc:
        log.error("Vérification IA : %s", exc)
        return False, None, f"Erreur lors de l'appel IA : {exc}"

    text = " ".join(b.get("text", "") for b in data.get("content", [])
                    if b.get("type") == "text").strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return False, None, "Réponse IA illisible (pas de JSON). Réessayez."
    try:
        result = json.loads(m.group(0))
    except json.JSONDecodeError:
        return False, None, "Réponse IA illisible (JSON invalide). Réessayez."

    report = str(result.get("report") or "").strip()
    ev = result.get("event") or {}
    complete = bool(result.get("complete")) and isinstance(ev, dict)
    if complete:
        missing = [f for f in _EVENT_FIELDS if f not in ev]
        core_empty = [f for f in ("name", "zone", "place", "when", "url", "desc")
                      if not str(ev.get(f, "")).strip()]
        if missing or core_empty:
            complete = False
            report = (report + f" [Contrôle serveur : champs manquants ou vides : "
                      f"{', '.join(missing + core_empty)}]").strip()
    if complete:
        ev = {f: ev.get(f, "") for f in _EVENT_FIELDS}  # 16 champs exactement
        return True, ev, report or "Fiche vérifiée et complétée."
    return False, None, report or "Vérification incomplète — aucun détail fourni par l'IA."


def _run_completion_job(key: str, candidate: dict, info: str) -> None:
    """Thread d'arrière-plan : vérifie le candidat et persiste le résultat."""
    try:
        complete, ev, report = _verify_candidate_with_ai(candidate, info)
        if complete:
            _save_completion(key, {"status": "done", "event": ev, "report": report})
            _push_completions()  # survit aux resets VM (comme les décisions)
            log.info("Complétion IA réussie : %s", ev.get("name", key))
        else:
            _save_completion(key, {"status": "failed", "report": report})
            log.info("Complétion IA incomplète (%s) : %s", key, report[:120])
    except Exception as exc:
        log.error("Complétion IA — erreur inattendue : %s", exc)
        _save_completion(key, {"status": "failed", "report": f"Erreur interne : {exc}"})


# ── Analytics — statistiques du site ─────────────────────────────────────────

_DATA_DIR       = "data"
_TRAFFIC_FILE   = os.path.join(_DATA_DIR, "traffic.json")
_QUESTIONS_FILE = os.path.join(_DATA_DIR, "chat_questions.jsonl")
_THEMES_FILE    = os.path.join(_DATA_DIR, "theme_analysis.json")
_PENDING_DIR    = "data/pending"
_DECISIONS_FILE = os.path.join(_DATA_DIR, "pending_decisions.json")

_traffic_lock   = threading.Lock()
_questions_lock = threading.Lock()
_decisions_lock = threading.Lock()

_CONF_RANK: dict = {"Vérifié": 0, "Probable": 1, "À confirmer": 2}

# IPs uniques vues aujourd'hui (reset automatique au changement de jour)
_today_ips:      set = set()
_today_date_str: str = ""

_THEMES_INTERVAL = 7 * 24 * 3600  # analyse hebdomadaire


_REF_SOURCES = [
    ("google",    ("google.", "bing.", "yahoo.", "duckduckgo.", "qwant.", "ecosia.")),
    ("facebook",  ("facebook.com", "fb.com")),
    ("instagram", ("instagram.com",)),
    ("whatsapp",  ("whatsapp.com",)),
]


def _categorize_referrer(referrer: str) -> str:
    """Classe l'URL de référence en une source simple."""
    if not referrer:
        return "direct"
    try:
        host = (urllib.parse.urlparse(referrer).hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        for src, patterns in _REF_SOURCES:
            if any(p in host for p in patterns):
                return src
        return "autre"
    except Exception:
        return "direct"


def _record_visit(ip: str, referrer: str = "") -> None:
    """Enregistre une visite sur le site public (thread-safe)."""
    global _today_ips, _today_date_str
    today = datetime.date.today().isoformat()
    ip_hash = hashlib.sha256(ip.encode()).hexdigest()[:16]
    src = _categorize_referrer(referrer)

    with _traffic_lock:
        if today != _today_date_str:
            _today_ips = set()
            _today_date_str = today
        is_new = ip_hash not in _today_ips
        _today_ips.add(ip_hash)
        try:
            os.makedirs(_DATA_DIR, exist_ok=True)
            try:
                with open(_TRAFFIC_FILE, encoding="utf-8") as f:
                    data = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                data = {}
            day = data.setdefault(today, {"v": 0, "u": 0, "refs": {}})
            day["v"] += 1
            if is_new:
                day["u"] += 1
            day.setdefault("refs", {})[src] = day["refs"].get(src, 0) + 1
            if len(data) > 365:
                for old in sorted(data)[:-365]:
                    del data[old]
            with open(_TRAFFIC_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception as exc:
            log.error("_record_visit : %s", exc)


def _record_question(text: str) -> None:
    """Enregistre une question du chatbot (append JSONL, thread-safe)."""
    try:
        os.makedirs(_DATA_DIR, exist_ok=True)
        entry = json.dumps({"ts": time.time(), "q": text[:300]}, ensure_ascii=False)
        with _questions_lock:
            with open(_QUESTIONS_FILE, "a", encoding="utf-8") as f:
                f.write(entry + "\n")
    except Exception as exc:
        log.error("_record_question : %s", exc)


def _run_theme_analysis() -> None:
    """Demande à Claude d'analyser les thèmes des questions des 30 derniers jours."""
    if not _ANTHROPIC_API_KEY:
        return
    cutoff = time.time() - 30 * 86400
    questions = []
    try:
        with _questions_lock:
            try:
                with open(_QUESTIONS_FILE, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                            if entry.get("ts", 0) >= cutoff:
                                questions.append(entry["q"])
                        except Exception:
                            pass
            except FileNotFoundError:
                pass
    except Exception as exc:
        log.error("_run_theme_analysis (lecture) : %s", exc)
        return

    if len(questions) < 3:
        log.info("Analyse thèmes : moins de 3 questions disponibles, abandon.")
        return False

    log.info("Analyse thèmes : analyse de %d questions.", len(questions))
    sample = questions[:200]
    questions_text = "\n".join(f"- {q}" for q in sample)
    prompt = (
        f"Voici {len(sample)} questions posées par des artisans réunionnais "
        f"à l'assistant « Le ti artisan futé » ces 30 derniers jours :\n\n"
        f"{questions_text}\n\n"
        "Identifie 5 à 8 thèmes récurrents. Pour chaque thème donne :\n"
        "- un nom court et clair (3-5 mots)\n"
        "- le nombre de questions estimé pour ce thème\n"
        "- une question représentative (courte, mot pour mot depuis la liste)\n\n"
        "Réponds UNIQUEMENT avec ce JSON valide (aucun texte avant ou après) :\n"
        '{"themes": [{"name": "...", "count": N, "example": "..."}, ...]}'
    )
    raw = _claude(
        _get_model("STRONG"),
        "Tu es un analyste de données. Réponds uniquement avec du JSON valide, aucun texte autour.",
        [{"role": "user", "content": prompt}],
    )
    try:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            raise ValueError("Pas de JSON dans la réponse Claude")
        result = json.loads(m.group())
        result["generated_at"]  = datetime.datetime.utcnow().isoformat()
        result["total_analyzed"] = len(questions)
        result["period_days"]    = 30
        os.makedirs(_DATA_DIR, exist_ok=True)
        with open(_THEMES_FILE, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        log.info("Analyse thèmes sauvegardée (%d thèmes).", len(result.get("themes", [])))
        return True
    except Exception as exc:
        log.error("_run_theme_analysis (sauvegarde) : %s", exc)
        return False


def _theme_analysis_loop() -> None:
    """Thread daemon : analyse hebdomadaire des thèmes de questions."""
    try:
        with open(_THEMES_FILE, encoding="utf-8") as f:
            last = json.load(f)
        gen = last.get("generated_at", "")
        if gen:
            age = time.time() - datetime.datetime.fromisoformat(gen).timestamp()
            if age < _THEMES_INTERVAL:
                time.sleep(_THEMES_INTERVAL - age)
    except Exception:
        pass  # Pas de fichier existant → lancer immédiatement
    while True:
        ok = _run_theme_analysis()
        # Si analyse ignorée (< 3 questions) ou échouée → réessai dans 1h
        # Sinon → attente hebdomadaire normale
        time.sleep(3600 if not ok else _THEMES_INTERVAL)


# ── Rendu de la page admin ─────────────────────────────────────────────────


def _load_traffic_stats() -> dict:
    try:
        with open(_TRAFFIC_FILE, encoding="utf-8") as f:
            raw = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        raw = {}
    today = datetime.date.today()
    days = []
    last7_v = last7_u = last30_v = last30_u = 0
    total_v = sum(d.get("v", 0) for d in raw.values())
    total_u = sum(d.get("u", 0) for d in raw.values())
    refs_total: dict = {}
    for i in range(30):
        d   = today - datetime.timedelta(days=i)
        key = d.isoformat()
        dd  = raw.get(key, {"v": 0, "u": 0})
        days.append({
            "date": key, "label": d.strftime("%-d %b"),
            "v": dd.get("v", 0), "u": dd.get("u", 0),
        })
        last30_v += dd.get("v", 0)
        last30_u += dd.get("u", 0)
        if i < 7:
            last7_v += dd.get("v", 0)
            last7_u += dd.get("u", 0)
        for src, cnt in dd.get("refs", {}).items():
            refs_total[src] = refs_total.get(src, 0) + cnt
    return {
        "days": days, "last7_v": last7_v, "last7_u": last7_u,
        "last30_v": last30_v, "last30_u": last30_u,
        "total_v": total_v, "total_u": total_u, "refs": refs_total,
    }


def _load_questions_stats() -> dict:
    total = 0
    last30 = 0
    cutoff = time.time() - 30 * 86400
    try:
        with _questions_lock:
            with open(_QUESTIONS_FILE, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    total += 1
                    try:
                        if json.loads(line).get("ts", 0) >= cutoff:
                            last30 += 1
                    except Exception:
                        pass
    except FileNotFoundError:
        pass
    return {"total": total, "last30": last30}


def _load_themes() -> dict:
    try:
        with open(_THEMES_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


# ── Propositions à valider ─────────────────────────────────────────────────

def _candidate_key(c: dict) -> str:
    """Clé stable (16 hex) identifiant un candidat de façon unique.

    Supporte les deux conventions de nommage :
      - clés préfixées (_source_url, _source_title, _confidence) — format Mac
      - clés sans préfixe (source_url, source_title, confidence)  — format legacy
    """
    ev_name = (c.get("event") or {}).get("name", "")
    src_url = c.get("_source_url") or c.get("source_url", "")
    src_ttl = c.get("_source_title") or c.get("source_title", "")
    raw = f"{ev_name}|{src_url}|{src_ttl}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _load_decisions() -> dict:
    """Charge {key: {decision, ts}} depuis le fichier de décisions persisté."""
    try:
        with _decisions_lock:
            with open(_DECISIONS_FILE, encoding="utf-8") as f:
                return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_decision(key: str, decision: str) -> None:
    """Enregistre 'published' ou 'rejected' pour un candidat (thread-safe)."""
    with _decisions_lock:
        try:
            with open(_DECISIONS_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            data = {}
        data[key] = {"decision": decision, "ts": time.time()}
        os.makedirs(_DATA_DIR, exist_ok=True)
        with open(_DECISIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


_COMPLETIONS_FILE = os.path.join(_DATA_DIR, "pending_completions.json")
_completions_lock = threading.Lock()

# Les 16 champs obligatoires d'une fiche événement complète.
_EVENT_FIELDS = ["name", "zone", "type", "org", "place", "when", "badge",
                 "month", "dateStatus", "status", "deadline", "contact",
                 "social", "url", "apply", "desc"]


def _load_completions() -> dict:
    """Charge {key: {status, event?, report?, ts}} (résultats de complétion IA)."""
    try:
        with _completions_lock:
            with open(_COMPLETIONS_FILE, encoding="utf-8") as f:
                return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_completion(key: str, entry: dict) -> None:
    """Enregistre le résultat de complétion d'un candidat (thread-safe)."""
    with _completions_lock:
        try:
            with open(_COMPLETIONS_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            data = {}
        entry["ts"] = time.time()
        data[key] = entry
        os.makedirs(_DATA_DIR, exist_ok=True)
        with open(_COMPLETIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


def _norm_evname(s: str) -> str:
    """Normalise un nom d'événement pour comparaison (dédoublonnage)."""
    s = str(s).lower().strip()
    s = re.sub(r"[—–‑]", "-", s)
    s = re.sub(r"\s+", " ", s)
    return s


def _published_name_zones() -> set:
    """Couples (nom normalisé, zone) des événements déjà dans events.json.

    events.json est versionné dans git : contrairement aux fichiers runtime,
    cette source de vérité survit aux resets de la VM de production.
    """
    try:
        with open(os.path.join("data", "events.json"), encoding="utf-8") as f:
            evs = json.load(f)
        return {(_norm_evname(e.get("name", "")), e.get("zone", "")) for e in evs}
    except Exception:
        return set()


def _push_runtime_file(relpath, commit_msg: str, label: str) -> None:
    """Commit + push un ou plusieurs fichiers runtime (str ou liste) pour
    qu'ils survivent aux resets VM.

    Non bloquant pour l'utilisateur : toute erreur est loguée, jamais fatale.
    Le token GitHub est masqué dans TOUS les chemins d'erreur.
    """
    gh_token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not gh_token or not _git_available():
        return

    def _mask(s: str) -> str:
        return str(s).replace(gh_token, "***")

    push_url = (f"https://x-access-token:{gh_token}"
                f"@github.com/FHSERVICES974/radar-marches-reunion.git")
    env = dict(os.environ)
    env.pop("GIT_ASKPASS", None)
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        paths = [relpath] if isinstance(relpath, str) else list(relpath)
        with _git_ops_lock:
            subprocess.run(["git", "add", *paths], check=True, timeout=30)
            c = subprocess.run(
                ["git", "-c", "user.email=admin@radar.re",
                 "-c", "user.name=Radar Admin",
                 "commit", "-m", commit_msg],
                capture_output=True, text=True, timeout=30,
            )
            if c.returncode != 0:
                return  # rien à committer
            ok, msg = _git_pull_for_publish()  # rebase doux (commits du Mac)
            if not ok:
                log.warning("Push %s — pull préalable échoué : %s",
                            label, _mask(msg)[:200])
            p = subprocess.run(
                ["git", "-c", "credential.helper=", "push", push_url, BRANCH],
                capture_output=True, text=True, timeout=60, env=env,
            )
            if p.returncode != 0:
                log.warning("Push %s échoué : %s",
                            label, _mask(p.stderr).strip()[:200])
    except Exception as exc:
        log.warning("Push %s : %s", label, _mask(exc)[:200])


def _push_decisions() -> None:
    _push_runtime_file("data/pending_decisions.json",
                       "Décisions propositions (Publier/Rejeter)",
                       "décisions")


def _push_completions() -> None:
    _push_runtime_file("data/pending_completions.json",
                       "Complétion IA d'un candidat (vérifié)",
                       "complétions")


# ── Soumissions organisateurs (page publique /organisateurs) ────────────────
#
# Principe : soumis ≠ publié. Une soumission atterrit dans une file de
# relecture (data/organizer_submissions.json) ; seul le propriétaire, depuis
# /admin, la publie via le MÊME chemin que les candidats IA
# (_publish_event_to_repo). Annuaire de contacts interne :
# data/organizer_contacts.json — jamais rendu sur le site public.

_SUBMISSIONS_FILE  = os.path.join(_DATA_DIR, "organizer_submissions.json")
_ORG_CONTACTS_FILE = os.path.join(_DATA_DIR, "organizer_contacts.json")
_submissions_lock  = threading.Lock()
_ORG_OWNER_EMAIL   = "shadowneox@gmail.com"

_ORG_RATE_MAX    = 5          # 5 soumissions / heure / IP
_ORG_RATE_WINDOW = 3600
_org_rate_store: dict = {}


def _check_org_rate(ip: str) -> bool:
    now = time.time()
    with _rate_lock:
        ts = [t for t in _org_rate_store.get(ip, []) if now - t < _ORG_RATE_WINDOW]
        if len(ts) >= _ORG_RATE_MAX:
            _org_rate_store[ip] = ts
            return False
        ts.append(now)
        _org_rate_store[ip] = ts
        return True


def _load_json_list(path: str) -> list:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def _save_json_list(path: str, data: list) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _push_org_files(paths: list) -> None:
    _push_runtime_file(paths, "Soumissions organisateurs (file de relecture)",
                       "soumissions organisateurs")


_FR_MONTHS = [("janv", 1), ("févr", 2), ("fevr", 2), ("mars", 3), ("avril", 4),
              ("avr", 4), ("mai", 5), ("juin", 6), ("juil", 7), ("août", 8),
              ("aout", 8), ("sept", 9), ("oct", 10), ("nov", 11), ("déc", 12),
              ("dec", 12)]
_MONTH_BADGES = {1: "JANV", 2: "FÉV", 3: "MARS", 4: "AVR", 5: "MAI", 6: "JUIN",
                 7: "JUIL", 8: "AOÛT", 9: "SEPT", 10: "OCT", 11: "NOV", 12: "DÉC"}


def _month_from_text(s: str) -> int:
    """Devine le mois (1-12) depuis un texte de date en français, sinon 99."""
    low = str(s).lower()
    for token, m in _FR_MONTHS:
        if token in low:
            return m
    return 99


def _norm_email(s: str) -> str:
    return str(s).strip().lower()


_SUBMIT_REQUIRED = ["name", "zone", "type", "org", "place", "when",
                    "links", "apply", "email", "desc",
                    "submitter_name", "submitter_phone"]
_ZONES = ["Nord", "Est", "Ouest", "Sud", "National"]


def _parse_links(raw: str) -> list:
    """Extrait les liens http(s) valides (un par ligne), max 5."""
    links = []
    for line in str(raw).splitlines():
        u = line.strip()
        if not u:
            continue
        if not u.startswith(("http://", "https://")):
            u = "https://" + u
        try:
            p = urllib.parse.urlparse(u)
            if p.scheme in ("http", "https") and p.netloc and "." in p.netloc:
                links.append(u)
        except ValueError:
            continue
    return links[:5]


def _submission_to_event(sub: dict) -> dict:
    """Construit la fiche 16 champs (même structure que les candidats IA)."""
    f = sub.get("fields", {})
    month = _month_from_text(f.get("when", ""))
    contact = f.get("email", "")
    if f.get("phone"):
        contact += " · " + f["phone"]
    ev = {k: "" for k in _EVENT_FIELDS}
    ev.update({
        "name":       f.get("name", ""),
        "zone":       f.get("zone", ""),
        "type":       f.get("type", ""),
        "org":        f.get("org", ""),
        "place":      f.get("place", ""),
        "when":       f.get("when", ""),
        "badge":      _MONTH_BADGES.get(month, ""),
        "month":      month,
        "dateStatus": "à confirmer",
        "status":     "open",
        "deadline":   f.get("deadline", ""),
        "contact":    contact,
        "social":     f.get("social", ""),
        "url":        (sub.get("links") or [""])[0],
        "apply":      f.get("apply", ""),
        "desc":       f.get("desc", ""),
    })
    return ev


def _update_org_contact(sub: dict) -> tuple:
    """Crée ou enrichit (sans écraser) le contact interne + l'annuaire orgs.json.

    Retourne (paths_modifiés: list, contact: dict).
    """
    f = sub.get("fields", {})
    email = _norm_email(f.get("email", ""))
    now_iso = datetime.datetime.now().strftime("%Y-%m-%d")
    paths = [_SUBMISSIONS_FILE.replace(os.sep, "/")]

    # 1 — Annuaire de contacts interne (privé, jamais rendu publiquement)
    contacts = _load_json_list(_ORG_CONTACTS_FILE)
    entry = next((c for c in contacts if _norm_email(c.get("email")) == email), None)
    if entry is None:
        entry = {"email": email, "name": f.get("org", ""),
                 "phone": f.get("phone", ""), "social": f.get("social", ""),
                 "first_contact": now_iso, "last_contact": now_iso,
                 "events_submitted": 1, "notes": ""}
        contacts.append(entry)
    else:
        # Enrichir uniquement les champs vides — jamais écraser
        for src, dst in (("org", "name"), ("phone", "phone"), ("social", "social")):
            if not str(entry.get(dst, "")).strip() and f.get(src):
                entry[dst] = f[src]
        entry["events_submitted"] = int(entry.get("events_submitted", 0)) + 1
        entry["last_contact"] = now_iso
    _save_json_list(_ORG_CONTACTS_FILE, contacts)
    paths.append(_ORG_CONTACTS_FILE.replace(os.sep, "/"))

    # 2 — Annuaire public orgs.json (même règle : compléter, jamais écraser)
    orgs_path = os.path.join(_DATA_DIR, "orgs.json")
    orgs = _load_json_list(orgs_path)
    org_entry = next((o for o in orgs if _norm_email(o.get("m")) == email), None)
    if org_entry is None:
        orgs.append({"n": f.get("org", ""), "m": email,
                     "t": f.get("phone", ""), "s": f.get("social", ""),
                     "w": (sub.get("links") or [""])[0]})
    else:
        for src_val, dst in ((f.get("phone", ""), "t"),
                             (f.get("social", ""), "s"),
                             ((sub.get("links") or [""])[0], "w")):
            if not str(org_entry.get(dst, "")).strip() and src_val:
                org_entry[dst] = src_val
    _save_json_list(orgs_path, orgs)
    paths.append("data/orgs.json")
    return paths, entry


_ORG_TYPES = ["Marché / Brocante", "Fête / Terroir", "Salon / Foire",
              "Fête patronale", "Marché de Noël", "Événement culturel"]


def _render_organisateurs_page(flash: str = "", form: dict = None) -> str:
    """Page publique /organisateurs — proposer un événement (file de relecture)."""
    form = form or {}

    def val(k):
        return html.escape(str(form.get(k, "")), quote=True)

    flash_html = ""
    if flash.startswith("ok"):
        flash_html = ('<div class="flash ok">✅ Merci ! Votre événement a bien été '
                      'transmis. Il sera relu par notre équipe avant toute mise en '
                      'ligne — rien n\'est publié automatiquement.</div>')
    elif flash.startswith("rate"):
        flash_html = ('<div class="flash err">⏳ Trop de soumissions depuis votre '
                      'connexion (5 max par heure). Réessayez un peu plus tard.</div>')
    elif flash.startswith("err:"):
        flash_html = f'<div class="flash err">❌ {html.escape(flash[4:])}</div>'

    zone_opts = "".join(
        f'<option value="{z}"{" selected" if form.get("zone") == z else ""}>{z}</option>'
        for z in _ZONES)
    type_opts = "".join(
        f'<option value="{t}"{" selected" if form.get("type_choice") == t else ""}>{t}</option>'
        for t in _ORG_TYPES)

    return f"""<!doctype html>
<html lang="fr"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Proposer un événement — Radar Marchés Réunion</title>
<meta name="description" content="Organisateurs : proposez votre marché, fête ou salon à La Réunion. Relecture avant publication.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,600;9..144,700&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  :root{{--bg:#f6f4ee;--panel:#fff;--ink:#211f1a;--muted:#8a8474;--line:#e7e1d2;
    --accent:#0e6b52;--accent-soft:#e7f2ed;--gold:#a9812f;--gold-soft:#f6efe0;
    --serif:'Fraunces',Georgia,serif;--sans:'Inter',-apple-system,"Segoe UI",Roboto,sans-serif}}
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:var(--sans);background:var(--bg);color:var(--ink);line-height:1.6}}
  .wrap{{max-width:760px;margin:0 auto;padding:34px 18px 60px}}
  .eyebrow{{color:var(--gold);font-weight:700;font-size:12px;letter-spacing:.14em;text-transform:uppercase}}
  h1{{font-family:var(--serif);font-weight:600;font-size:34px;letter-spacing:-.3px;margin:8px 0 10px}}
  .lede{{color:var(--muted);font-size:15.5px;max-width:56ch}}
  .notice{{margin:20px 0 26px;background:var(--gold-soft);border:1px solid #e8d9b4;border-radius:12px;
    padding:13px 16px;font-size:14px}}
  .flash{{margin:0 0 20px;border-radius:12px;padding:13px 16px;font-size:14.5px}}
  .flash.ok{{background:var(--accent-soft);border:1px solid #bfdccf}}
  .flash.err{{background:#f4e9e6;border:1px solid #e2c6bf}}
  form{{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:26px 24px;
    box-shadow:0 1px 2px rgba(30,26,15,.04),0 10px 28px rgba(30,26,15,.06)}}
  fieldset{{border:0;margin:0 0 8px}}
  legend{{font-family:var(--serif);font-size:19px;font-weight:600;color:var(--accent);margin:14px 0 12px}}
  label{{display:block;font-weight:600;font-size:13.5px;margin:14px 0 5px}}
  label .opt{{color:var(--muted);font-weight:400}}
  input,select,textarea{{width:100%;border:1px solid var(--line);border-radius:10px;background:#fdfcf9;
    padding:10px 12px;font-size:14.5px;font-family:var(--sans);color:var(--ink)}}
  input:focus,select:focus,textarea:focus{{outline:2px solid var(--accent);outline-offset:0;border-color:var(--accent)}}
  textarea{{min-height:74px;resize:vertical}}
  .hint{{font-size:12.5px;color:var(--muted);margin-top:4px}}
  .grid2{{display:grid;grid-template-columns:1fr 1fr;gap:0 16px}}
  @media(max-width:560px){{.grid2{{grid-template-columns:1fr}}}}
  .hp{{position:absolute;left:-6000px;top:-6000px;height:1px;overflow:hidden}}
  button{{margin-top:22px;width:100%;background:var(--accent);color:#fff;border:0;border-radius:12px;
    padding:14px;font-size:15.5px;font-weight:700;font-family:var(--sans);cursor:pointer}}
  button:hover{{background:#0b573f}}
  .back{{display:inline-block;margin-bottom:18px;color:var(--accent);font-weight:600;font-size:13.5px;text-decoration:none}}
  .foot{{margin-top:18px;font-size:12.5px;color:var(--muted)}}
</style></head><body><div class="wrap">
<a class="back" href="/">← Retour au radar des marchés</a>
<div class="eyebrow">Espace organisateurs</div>
<h1>Proposez votre événement</h1>
<p class="lede">Marché, fête, salon, brocante… à La Réunion ? Transmettez-nous les
informations : nous vérifions chaque proposition avant de l'ajouter au radar.</p>
<div class="notice">📋 <strong>Soumis n'est pas publié</strong> : votre proposition
rejoint une file de relecture. Rien n'apparaît sur le site sans validation manuelle.</div>
{flash_html}
<form method="post" action="/organisateurs" novalidate>
  <div class="hp" aria-hidden="true">
    <label>Ne pas remplir<input type="text" name="website" tabindex="-1" autocomplete="off"></label>
  </div>
  <fieldset><legend>L'événement</legend>
    <label>Nom de l'événement *<input name="name" required maxlength="120" value="{val('name')}"></label>
    <div class="grid2">
      <label>Zone *<select name="zone" required>
        <option value="" disabled{" selected" if not form.get("zone") else ""}>Choisir…</option>{zone_opts}
      </select></label>
      <label>Type d'événement *<select name="type_choice">
        {type_opts}<option value="Autre"{" selected" if form.get("type_choice") == "Autre" else ""}>Autre (précisez)</option>
      </select></label>
    </div>
    <label>Si « Autre » : précisez le type <span class="opt">(sinon laissez vide)</span>
      <input name="type_autre" maxlength="60" value="{val('type_autre')}"></label>
    <label>Organisateur / organisation *<input name="org" required maxlength="120" value="{val('org')}"></label>
    <label>Lieu précis *<input name="place" required maxlength="160"
      placeholder="Ex. : Jardins de la plage, Saint-Pierre" value="{val('place')}"></label>
    <div class="grid2">
      <label>Date ou période *<input name="when" required maxlength="160"
        placeholder="Ex. : 12–14 septembre 2026" value="{val('when')}"></label>
      <label>Date limite de candidature <span class="opt">(recommandé)</span>
        <input name="deadline" maxlength="160" value="{val('deadline')}"></label>
    </div>
    <label>Lien(s) vers l'événement * <span class="opt">— un par ligne</span>
      <textarea name="links" required placeholder="Page officielle, post Instagram ou Facebook…">{val('links')}</textarea>
      <div class="hint">C'est le champ clé : il nous permet de vérifier et compléter votre fiche.</div></label>
    <label>Comment candidater ? *<textarea name="apply" required maxlength="600"
      placeholder="Ex. : dossier à envoyer par email avant le 30 juin…">{val('apply')}</textarea></label>
    <label>Description courte (2-3 phrases) *<textarea name="desc" required maxlength="600">{val('desc')}</textarea></label>
  </fieldset>
  <fieldset><legend>Contact organisateur</legend>
    <div class="grid2">
      <label>Email de contact *<input type="email" name="email" required maxlength="120" value="{val('email')}"></label>
      <label>Téléphone <span class="opt">(facultatif)</span>
        <input name="phone" maxlength="40" value="{val('phone')}"></label>
    </div>
    <label>Compte réseaux sociaux <span class="opt">(facultatif — ex. @moncompte)</span>
      <input name="social" maxlength="120" value="{val('social')}"></label>
  </fieldset>
  <fieldset><legend>Vos coordonnées (personne qui soumet)</legend>
    <div class="grid2">
      <label>Votre nom *<input name="submitter_name" required maxlength="120" value="{val('submitter_name')}"></label>
      <label>Votre téléphone *<input name="submitter_phone" required maxlength="40" value="{val('submitter_phone')}"></label>
    </div>
    <div class="hint">Usage interne uniquement — jamais publié sur le site.</div>
  </fieldset>
  <button type="submit">Envoyer ma proposition</button>
</form>
<script>
document.querySelector('form[action="/organisateurs"]').addEventListener('submit', function () {{
  var b = this.querySelector('button[type=submit]');
  b.disabled = true; b.textContent = 'Envoi en cours…';
}});
</script>
<p class="foot">Vos coordonnées servent uniquement à la vérification et au suivi
de votre proposition. Aucune donnée personnelle n'est publiée sans votre accord.</p>
</div></body></html>"""


def _render_org_submissions_section() -> str:
    """Section /admin : soumissions organisateurs en attente de relecture."""
    esc = lambda s: html.escape(str(s), quote=True)  # noqa: E731
    subs = _load_json_list(_SUBMISSIONS_FILE)
    pending = [s for s in subs if s.get("status") == "pending"]
    contacts = {_norm_email(c.get("email")): c for c in _load_json_list(_ORG_CONTACTS_FILE)}

    header = ('<div class="card"><div class="card-h">📨 Soumissions organisateurs'
              f' <span style="color:#8a8474;font-weight:400">({len(pending)} en attente)</span>'
              ' — <a href="/admin/contacts.csv" style="font-size:13px">Exporter les contacts (CSV)</a></div>')
    if not pending:
        return header + ('<div class="empty-st"><span>📭</span>'
                         '<p>Aucune soumission en attente.</p>'
                         '<span class="empty-sub">Les organisateurs peuvent proposer '
                         'leurs événements sur <a href="/organisateurs">/organisateurs</a>.</span>'
                         '</div></div>')

    cards = []
    for s in pending:
        f = s.get("fields", {})
        email = _norm_email(f.get("email", ""))
        known = contacts.get(email)
        if known and int(known.get("events_submitted", 0)) > 0:
            n = int(known["events_submitted"])
            badge = (f'<span class="conf-badge" style="background:#e7f2ed;color:#0e6b52">'
                     f'✔ Organisateur connu — {n} événement{"s" if n > 1 else ""} soumis</span>')
        else:
            badge = ('<span class="conf-badge" style="background:#f6efe0;color:#a9812f">'
                     '🆕 Nouvel organisateur</span>')
        links_html = " ".join(
            f'<a href="{esc(u)}" target="_blank" rel="noopener noreferrer">{esc(u)}</a><br>'
            for u in s.get("links", []) if str(u).startswith(("http://", "https://")))
        rows = "".join(
            f"<div><strong>{lbl} :</strong> {esc(f.get(k, '')) or '<em>—</em>'}</div>"
            for lbl, k in [("Zone", "zone"), ("Type", "type"), ("Organisateur", "org"),
                           ("Lieu", "place"), ("Date/période", "when"),
                           ("Date limite", "deadline"), ("Candidature", "apply"),
                           ("Email", "email"), ("Téléphone", "phone"),
                           ("Réseaux", "social"), ("Description", "desc")])
        cards.append(f"""
<div class="prop-card">
  <div style="display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;align-items:center">
    <strong style="font-size:15px">{esc(f.get('name', '(sans nom)'))}</strong>{badge}
  </div>
  <div style="font-size:13.5px;margin-top:8px;display:grid;gap:3px">{rows}
    <div><strong>Lien(s) :</strong><br>{links_html or '<em>—</em>'}</div>
    <div><strong>Soumis par :</strong> {esc(s.get('submitter_name', ''))}
         ({esc(s.get('submitter_phone', ''))}) — {esc(str(s.get('ts', ''))[:16])}</div>
  </div>
  <form method="post" action="/admin/org-approve" style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap">
    <input type="hidden" name="id" value="{esc(s.get('id', ''))}">
    <input name="note" placeholder="Note (optionnelle)" maxlength="300"
           style="flex:1;min-width:180px;border:1px solid #e7e1d2;border-radius:8px;padding:7px 10px;font-size:13px">
    <button class="btn-pub" type="submit">✅ Valider &amp; publier</button>
    <button class="btn-rej" type="submit" formaction="/admin/org-reject">❌ Rejeter</button>
  </form>
</div>""")
    return header + "".join(cards) + "</div>"


def _load_latest_proposal() -> tuple:
    """Lit le fichier de proposition le plus récent (tri alphabétique desc).

    Retourne (filename, candidates) ou (None, []).
    """
    try:
        files = sorted(
            [fn for fn in os.listdir(_PENDING_DIR) if fn.endswith(".json")],
            reverse=True,
        )
        if not files:
            return None, []
        path = os.path.join(_PENDING_DIR, files[0])
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        # Supporte "new_events_candidates" (format Mac) et "candidates" (legacy)
        candidates = data.get("new_events_candidates") or data.get("candidates", [])
        return files[0], candidates
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None, []


def _git_pull_for_publish() -> tuple:
    """Pull doux (rebase) depuis GitHub avant d'écrire les données.

    Retourne (ok: bool, message: str).
    """
    if not _git_available():
        return False, "Dépôt git absent."
    try:
        r = subprocess.run(
            ["git", "pull", "--rebase", "--autostash", "origin", BRANCH],
            capture_output=True, text=True, timeout=60,
        )
        if r.returncode != 0:
            return False, r.stderr.strip() or "git pull échoué."
        return True, r.stdout.strip()
    except subprocess.TimeoutExpired:
        return False, "git pull expiré (>60 s)."
    except Exception as exc:
        return False, str(exc)


def _publish_event_to_repo(event: dict) -> tuple:
    """Publication sérialisée : voir _publish_event_to_repo_unlocked."""
    with _git_ops_lock:
        return _publish_event_to_repo_unlocked(event)


def _publish_event_to_repo_unlocked(event: dict) -> tuple:
    """Ajoute l'événement à events.json, rebuild index.html, commit et push.

    Flux : git pull → insert + sort events.json → maj meta.json → build() →
           git add → git commit → git push.
    Retourne (ok: bool, message: str).
    """
    # 1 — Pull d'abord (intègre les commits éventuels du Mac)
    ok, msg = _git_pull_for_publish()
    if not ok:
        return False, f"Synchronisation GitHub impossible : {msg}"

    events_path = os.path.join("data", "events.json")
    meta_path   = os.path.join("data", "meta.json")

    # 2 — Charger events.json
    try:
        with open(events_path, encoding="utf-8") as f:
            events = json.load(f)
    except Exception as exc:
        return False, f"Lecture events.json impossible : {exc}"

    # 3 — Dédupliquer (nom exact, insensible à la casse)
    ev_name = event.get("name", "").strip().lower()
    if any(e.get("name", "").strip().lower() == ev_name for e in events):
        return False, f"« {event.get('name')} » existe déjà dans events.json."

    # 4 — Insérer et trier par mois puis nom
    events.append(event)
    events.sort(key=lambda e: (e.get("month", 99), e.get("name", "")))

    # 5 — Écrire events.json
    try:
        with open(events_path, "w", encoding="utf-8") as f:
            json.dump(events, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        return False, f"Écriture events.json impossible : {exc}"

    # 6 — Mettre à jour meta.json (lastUpdate)
    try:
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
        meta["lastUpdate"] = datetime.datetime.now().strftime("%Y-%m-%d")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        log.warning("meta.json non mis à jour : %s", exc)

    # 7 — Rebuild index.html depuis template.html (le design est figé — jamais modifié)
    try:
        import importlib
        import build as _build_mod
        importlib.reload(_build_mod)
        _build_mod.build()
    except Exception as exc:
        return False, f"Rebuild index.html échoué : {exc}"

    # 8 — Vérification du token puis commit + push
    gh_token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not gh_token:
        return False, (
            "Secret GITHUB_TOKEN manquant. "
            "Ajoutez un Personal Access Token GitHub dans les secrets Replit "
            "sous la clé GITHUB_TOKEN, puis republier."
        )

    # Vérifier le token via l'API REST avant de toucher à git.
    # Évite un commit orphelin si le token est mauvais.
    try:
        import urllib.request as _ureq
        _req = _ureq.Request(
            "https://api.github.com/repos/FHSERVICES974/radar-marches-reunion",
            headers={
                "Authorization": f"token {gh_token}",
                "User-Agent": "radar-admin/1.0",
                "Accept": "application/vnd.github+json",
            },
        )
        with _ureq.urlopen(_req, timeout=10) as _r:
            _repo = json.loads(_r.read().decode())
        if not _repo.get("permissions", {}).get("push", False):
            return False, (
                "GITHUB_TOKEN valide mais sans droit d'écriture. "
                "Le token doit avoir le scope 'repo' (classic) "
                "ou 'Contents: Read & write' sur ce dépôt (fine-grained)."
            )
    except Exception as _api_err:
        _msg = str(_api_err)
        if "401" in _msg:
            return False, (
                "GITHUB_TOKEN invalide ou expiré (erreur 401). "
                "Créez un nouveau token sur https://github.com/settings/tokens "
                "et mettez-le à jour dans les secrets Replit."
            )
        if "403" in _msg:
            return False, (
                "GITHUB_TOKEN refusé par GitHub (erreur 403). "
                "Vérifiez que le token appartient au compte FHSERVICES974 "
                "et qu'il a accès à ce dépôt."
            )
        # Erreur réseau inattendue : on tente quand même le push
        log.warning("Vérification API GitHub échouée (%s) — push tenté quand même.", _msg)

    ev_label = event.get("name", "événement")
    # Forme x-access-token vérifiée fonctionnelle avec ce token
    # (le header "Authorization: token …" est refusé par GitHub).
    push_url = (f"https://x-access-token:{gh_token}"
                f"@github.com/FHSERVICES974/radar-marches-reunion.git")
    try:
        subprocess.run(
            ["git", "add", "data/events.json", "data/meta.json", "index.html"],
            check=True, timeout=30,
        )
        subprocess.run(
            ["git", "-c", "user.email=admin@radar.re",
             "-c", "user.name=Radar Admin",
             "commit", "-m", f"Publier : {ev_label}"],
            check=True, timeout=30,
        )
        push_env = dict(os.environ)
        push_env.pop("GIT_ASKPASS", None)      # jamais replit-git-askpass
        push_env["GIT_TERMINAL_PROMPT"] = "0"  # échec propre au lieu d'un prompt
        push = subprocess.run(
            ["git", "-c", "credential.helper=",
             "push", push_url, BRANCH],
            capture_output=True, text=True, timeout=60, env=push_env,
        )
        if push.returncode != 0:
            err = push.stderr.replace(gh_token, "***").strip()
            return False, f"git push échoué : {err}"
    except subprocess.CalledProcessError as exc:
        return False, f"Opération git échouée : {exc}"
    except subprocess.TimeoutExpired:
        return False, "git push expiré (>60 s)."

    log.info("Publié et pushé : %s", ev_label)
    return True, f"« {ev_label} » publié et pushé sur GitHub."


def _render_proposals_section(dev_mode: bool) -> str:  # noqa: PLR0912,PLR0915
    """Génère la section 'Propositions à valider' du dashboard admin."""
    filename, candidates = _load_latest_proposal()
    decisions = _load_decisions()

    if not candidates:
        hint = ("Les fichiers de veille apparaissent ici dès qu'ils arrivent via GitHub Sync."
                if not filename else "Le fichier ne contient aucun candidat.")
        return (
            '<div class="card">'
            '<div class="card-h">📥 Propositions à valider</div>'
            '<div class="empty-st"><span>✅</span>'
            '<p>Aucune proposition en attente.</p>'
            f'<span class="empty-sub">{hint}</span>'
            '</div></div>'
        )

    # Filtrer les déjà traités, trier par confiance
    pending = [c for c in candidates if _candidate_key(c) not in decisions]

    # Appliquer les complétions IA validées (overlay local).
    # La clé d'origine est figée AVANT la fusion : _candidate_key dépend de
    # event.name, qui peut changer après complétion.
    completions = _load_completions()
    for c in pending:
        orig_key = _candidate_key(c)
        c["_orig_key"] = orig_key
        comp = completions.get(orig_key)
        if comp and comp.get("status") == "done" and comp.get("event"):
            c["event"] = comp["event"]
            c["_confidence"] = "Vérifié"

    # Dédoublonnage durable : masquer les candidats dont l'événement figure
    # déjà dans events.json (survit aux resets VM, contrairement aux décisions).
    published = _published_name_zones()
    pending = [
        c for c in pending
        if not (c.get("event") or {}).get("name")
        or (_norm_evname(c["event"]["name"]), c["event"].get("zone", "")) not in published
    ]

    if not pending:
        return (
            '<div class="card">'
            '<div class="card-h">📥 Propositions à valider</div>'
            '<div class="empty-st"><span>✅</span>'
            '<p>Tout est traité — file vide.</p>'
            '<span class="empty-sub">Nouvelle veille attendue demain.</span>'
            '</div></div>'
        )

    pending.sort(key=lambda c: _CONF_RANK.get(c.get("_confidence") or c.get("confidence", "À confirmer"), 3))

    # Compteurs
    n_pub = sum(1 for c in pending if (c.get("_confidence") or c.get("confidence")) == "Vérifié" and c.get("event"))
    n_chk = len(pending) - n_pub
    counts = []
    if n_pub:
        s = "s" if n_pub > 1 else ""
        counts.append(f'<span class="prop-cnt-pub">{n_pub} prête{s} à publier</span>')
    if n_chk:
        counts.append(f'<span class="prop-cnt-chk">{n_chk} à vérifier</span>')
    count_html = "&nbsp;·&nbsp;".join(counts)

    label   = filename.replace(".json", "").replace("propositions_", "").replace("_", "\u00a0")
    from_tag = f'<span class="prop-from">— {label}</span>' if filename else ""

    # Cartes
    cards = []
    esc = lambda s: (str(s).replace("&", "&amp;").replace("<", "&lt;")  # noqa: E731
                     .replace(">", "&gt;").replace('"', "&quot;"))
    for c in pending:
        key     = c.get("_orig_key") or _candidate_key(c)
        conf    = c.get("_confidence") or c.get("confidence", "À confirmer")
        ev      = c.get("event") or {}
        has_ev  = bool(ev and ev.get("name"))
        name    = esc(ev.get("name") or c.get("_source_title") or c.get("source_title") or "—")
        place   = esc(ev.get("place", ""))
        when    = esc(ev.get("when", ""))
        dl      = esc(ev.get("deadline", ""))
        notes   = esc(c.get("notes", ""))
        src_url = c.get("_source_url") or c.get("source_url", "#")
        if not src_url.startswith(("http://", "https://")):
            src_url = "#"
        src_url = esc(src_url)
        src_ttl = esc((c.get("_source_title") or c.get("source_title") or src_url)[:55])

        if conf == "Vérifié":
            badge = '<span class="conf-badge conf-vert">✓ Vérifié</span>'
        elif conf == "Probable":
            badge = '<span class="conf-badge conf-amb">~ Probable</span>'
        else:
            badge = '<span class="conf-badge conf-grey">? À confirmer</span>'

        meta_items = []
        if place: meta_items.append(f"📍 {place}")
        if when:  meta_items.append(f"📅 {when}")
        if dl:    meta_items.append(f"⏰ {dl[:70]}")
        meta_html = "".join(f'<span class="prop-meta-item">{x}</span>' for x in meta_items)

        notes_html = (f'<div class="prop-notes">{notes[:200]}</div>'
                      if notes and not has_ev else "")

        pub_btn = (
            f'<form method="POST" action="/admin/publish" class="prop-form">'
            f'<input type="hidden" name="key" value="{key}">'
            f'<button type="submit" class="btn-prop btn-pub">📤 Publier</button>'
            f'</form>'
        ) if has_ev else ""

        rej_btn = (
            f'<form method="POST" action="/admin/reject" class="prop-form">'
            f'<input type="hidden" name="key" value="{key}">'
            f'<button type="submit" class="btn-prop btn-rej">✕ Rejeter</button>'
            f'</form>'
        )

        # Bloc « compléter » pour les candidats sans fiche complète
        complete_html = ""
        if not has_ev:
            comp = completions.get(key)
            if comp and comp.get("status") == "running":
                complete_html = ('<div class="prop-running">⏳ Vérification IA en cours… '
                                 'rechargez la page dans une minute.</div>')
            else:
                report_html = ""
                if comp and comp.get("status") == "failed" and comp.get("report"):
                    rep = (comp["report"].replace("&", "&amp;")
                           .replace("<", "&lt;").replace(">", "&gt;"))
                    report_html = f'<div class="prop-report">🔎 {rep}</div>'
                complete_html = (
                    f'{report_html}'
                    f'<form method="POST" action="/admin/complete" class="prop-complete">'
                    f'<input type="hidden" name="key" value="{key}">'
                    f'<input type="text" name="info" class="inp-comp" maxlength="600" '
                    f'placeholder="URL de l\'annonce officielle, contact, date confirmée…">'
                    f'<button type="submit" class="btn-prop btn-comp">🔎 Vérifier et compléter</button>'
                    f'</form>'
                )

        cards.append(
            f'<div class="prop-card">'
            f'<div class="prop-top">{badge}<div class="prop-name">{name}</div></div>'
            f'<div class="prop-meta">{meta_html}</div>'
            f'{notes_html}'
            f'{complete_html}'
            f'<div class="prop-foot">'
            f'<a href="{src_url}" target="_blank" rel="noopener" class="prop-src">🔗 {src_ttl}</a>'
            f'<div class="prop-actions">{pub_btn}{rej_btn}</div>'
            f'</div></div>'
        )

    return (
        f'<div class="card">'
        f'<div class="card-h">📥 Propositions à valider {from_tag}</div>'
        f'<div class="prop-summary">{count_html}</div>'
        f'<div class="prop-list">' + "\n".join(cards) + '</div>'
        f'</div>'
    )


_CLICKS_FILE  = os.path.join(_DATA_DIR, "clicks.jsonl")
_clicks_lock  = threading.Lock()


def _record_click(event: str, name: str = "") -> None:
    """Enregistre un clic de l'utilisateur (append JSONL, thread-safe)."""
    try:
        os.makedirs(_DATA_DIR, exist_ok=True)
        entry = json.dumps({"ts": time.time(), "e": event, "n": name[:80]}, ensure_ascii=False)
        with _clicks_lock:
            with open(_CLICKS_FILE, "a", encoding="utf-8") as f:
                f.write(entry + "\n")
    except Exception as exc:
        log.error("_record_click : %s", exc)


def _load_clicks_stats() -> dict:
    """Charge les statistiques de clics des 30 derniers jours."""
    totals: dict = {"chatbot_open": 0, "candidater": 0, "event_view": 0}
    top_events: dict = {}
    top_cand: dict = {}
    cutoff = time.time() - 30 * 86400
    try:
        with _clicks_lock:
            with open(_CLICKS_FILE, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        if entry.get("ts", 0) < cutoff:
                            continue
                        ev   = entry.get("e", "")
                        name = entry.get("n", "").strip()
                        if ev in totals:
                            totals[ev] += 1
                        if ev == "event_view" and name:
                            top_events[name] = top_events.get(name, 0) + 1
                        if ev == "candidater" and name:
                            top_cand[name] = top_cand.get(name, 0) + 1
                    except Exception:
                        pass
    except FileNotFoundError:
        pass
    return {
        **totals,
        "top_events": sorted(top_events.items(), key=lambda x: x[1], reverse=True)[:8],
        "top_cand":   sorted(top_cand.items(),   key=lambda x: x[1], reverse=True)[:5],
    }


# ── Session admin (Replit Auth PKCE flow) ────────────────────────────────────

_SESSION_SECRET  = os.environ.get("SESSION_SECRET", "fallback-dev-secret")
_SESSION_COOKIE  = "radar_admin_sid"
_SESSION_TTL     = 12 * 3600   # 12 h


def _make_session_token(username: str) -> str:
    """Crée un token de session signé avec SESSION_SECRET (HMAC-SHA256)."""
    ts      = str(int(time.time()))
    payload = f"{username}:{ts}"
    sig     = hmac.new(_SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    return base64.urlsafe_b64encode(f"{payload}:{sig}".encode()).decode()


def _verify_session_token(token: str) -> str | None:
    """Vérifie le token ; retourne le username si valide et non expiré, sinon None."""
    try:
        decoded = base64.urlsafe_b64decode(token + "==").decode()
        username, ts_str, sig = decoded.rsplit(":", 2)
        if time.time() - int(ts_str) > _SESSION_TTL:
            return None
        expected = hmac.new(_SESSION_SECRET.encode(), f"{username}:{ts_str}".encode(),
                            hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(sig, expected):
            return None
        return username
    except Exception:
        return None


def _get_session_cookie(headers) -> str | None:
    """Extrait la valeur du cookie de session depuis les headers HTTP."""
    for part in headers.get("Cookie", "").split(";"):
        part = part.strip()
        if part.startswith(f"{_SESSION_COOKIE}="):
            return part[len(f"{_SESSION_COOKIE}="):]
    return None


def _parse_replit_auth_response(raw: str) -> dict:
    """Décode le authResponse renvoyé par Replit (base64url → JSON)."""
    try:
        # Ajoute le padding manquant puis décode
        padded = raw + "=" * (-len(raw) % 4)
        return json.loads(base64.urlsafe_b64decode(padded).decode())
    except Exception:
        return {}


def _render_auth_required(error: str = "") -> str:
    err_html = f'<p class="err">{error}</p>' if error else ""
    return f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Accès restreint</title>
<style>
  *{{box-sizing:border-box}}
  body{{font-family:system-ui,sans-serif;background:#f9fafb;display:flex;
       align-items:center;justify-content:center;min-height:100vh;margin:0}}
  .card{{background:#fff;border:1px solid #e5e7eb;border-radius:12px;
         padding:2.5rem 2rem;max-width:380px;width:90%;text-align:center;
         box-shadow:0 2px 8px rgba(0,0,0,.06)}}
  h1{{font-size:1.1rem;color:#111827;margin:0 0 .4rem}}
  p{{color:#6b7280;font-size:.875rem;line-height:1.5;margin:0 0 1.2rem}}
  .err{{color:#dc2626;background:#fef2f2;border:1px solid #fecaca;border-radius:6px;
        padding:.5rem .8rem;font-size:.85rem;margin-bottom:1rem;text-align:left}}
  input[type=password]{{width:100%;padding:.6rem .8rem;border:1px solid #d1d5db;
    border-radius:8px;font-size:.9rem;margin-bottom:.9rem;outline:none}}
  input[type=password]:focus{{border-color:#2563eb;box-shadow:0 0 0 3px rgba(37,99,235,.15)}}
  .btn{{width:100%;background:#2563eb;color:#fff;padding:.65rem;border-radius:8px;
        font-size:.875rem;font-weight:500;cursor:pointer;border:none}}
  .btn:hover{{background:#1d4ed8}}
</style></head>
<body>
  <div class="card">
    <div style="font-size:2rem;margin-bottom:.6rem">🔒</div>
    <h1>Espace propriétaire</h1>
    <p>Entrez le mot de passe pour accéder aux statistiques.</p>
    {err_html}
    <form method="POST" action="/admin/login">
      <input type="password" name="password" placeholder="Mot de passe" autofocus required>
      <button class="btn" type="submit">Accéder</button>
    </form>
  </div>
</body></html>"""


def _render_stats_page(dev_mode: bool, user_name: str, flash: str = "") -> str:  # noqa: PLR0912,PLR0915
    traffic        = _load_traffic_stats()
    q_stats        = _load_questions_stats()
    themes         = _load_themes()
    clicks         = _load_clicks_stats()
    proposals_html = _render_proposals_section(dev_mode)
    org_subs_html  = _render_org_submissions_section()
    now_str        = datetime.datetime.now().strftime("%d/%m/%Y à %H:%M")

    # Flash message HTML
    flash_html = ""
    if flash.startswith("ok:"):
        flash_html = f'<div class="flash flash-ok">{flash[3:]}</div>'
    elif flash.startswith("err:"):
        flash_html = f'<div class="flash flash-err">{flash[4:]}</div>'

    # ── Chart: traffic 14 days oldest→newest, skip leading zeros ──────────────
    days14    = list(reversed(traffic["days"][:14]))
    first_nz  = next((i for i, d in enumerate(days14) if d["v"] > 0), len(days14) - 1)
    chart_d   = days14[max(0, first_nz - 1):]
    chart_lbs = json.dumps([d["label"] for d in chart_d])
    chart_v   = json.dumps([d["v"]     for d in chart_d])
    chart_u   = json.dumps([d["u"]     for d in chart_d])

    # ── Referrer sources ───────────────────────────────────────────────────────
    refs       = traffic.get("refs", {})
    ref_order  = ["direct", "google", "facebook", "instagram", "whatsapp", "autre"]
    ref_labels = {"direct":"Lien direct","google":"Recherche","facebook":"Facebook",
                  "instagram":"Instagram","whatsapp":"WhatsApp","autre":"Autre"}
    ref_colors = {"direct":"#6366f1","google":"#f59e0b","facebook":"#3b82f6",
                  "instagram":"#ec4899","whatsapp":"#22c55e","autre":"#94a3b8"}
    refs_data  = [(ref_labels[k], refs.get(k, 0), ref_colors[k])
                  for k in ref_order if refs.get(k, 0) > 0]
    has_refs   = bool(refs_data)
    refs_lj    = json.dumps([r[0] for r in refs_data])
    refs_vj    = json.dumps([r[1] for r in refs_data])
    refs_cj    = json.dumps([r[2] for r in refs_data])

    # ── Referrer legend HTML ───────────────────────────────────────────────────
    ref_legend = ""
    if has_refs:
        ref_legend = '<div class="ref-leg">'
        for lbl, cnt, col in refs_data:
            ref_legend += (
                f'<div class="ref-row"><span class="ref-dot" style="background:{col}"></span>'
                f'<span class="ref-lbl">{lbl}</span><span class="ref-cnt">{cnt}</span></div>'
            )
        ref_legend += '</div>'
    refs_canvas = '<canvas id="refChart"></canvas>' if has_refs else \
                  '<div class="no-chart">Aucune source enregistrée pour le moment</div>'

    # ── Themes ─────────────────────────────────────────────────────────────────
    theme_list = (themes or {}).get("themes", [])
    max_cnt    = max((t.get("count", 0) for t in theme_list), default=1) or 1
    if theme_list:
        gen_at = (themes or {}).get("generated_at", "")
        try:    gen_date = datetime.datetime.fromisoformat(gen_at).strftime("%d/%m/%Y")
        except: gen_date = gen_at[:10] if gen_at else "—"  # noqa: E722
        themes_body = (
            f'<div class="th-meta">Analyse du {gen_date} &nbsp;·&nbsp; '
            f'{(themes or {}).get("total_analyzed","?")} questions &nbsp;·&nbsp; 30 derniers jours</div>'
            '<div class="th-list">'
        )
        for t in sorted(theme_list, key=lambda x: x.get("count", 0), reverse=True):
            cnt  = t.get("count", 0)
            pct  = round(cnt / max_cnt * 100)
            themes_body += (
                f'<div class="ti"><div class="ti-row">'
                f'<span class="ti-name">{t.get("name","?")}</span>'
                f'<span class="ti-badge">{cnt} q.</span></div>'
                f'<div class="ti-bar"><div class="ti-fill" style="width:{pct}%"></div></div>'
                f'<div class="ti-ex">{t.get("example","")}</div></div>'
            )
        themes_body += (
            f'</div><p class="hint-xs">Prochaine analyse dans ~'
            f'{round(_THEMES_INTERVAL / 3600 / 24)} jours.</p>'
        )
    else:
        themes_body = (
            '<div class="empty-st"><span>📭</span>'
            '<p>Pas encore d\'analyse disponible.</p>'
            '<span class="empty-sub">L\'analyse se déclenche dès 3 questions, puis toutes les 7 jours.</span>'
            '</div>'
            '<form method="POST" action="/admin/run-analysis" style="margin-top:.8rem;text-align:center">'
            '<button type="submit" style="background:#2563eb;color:#fff;border:none;padding:.45rem 1.1rem;'
            'border-radius:6px;font-size:.85rem;cursor:pointer">⚡ Lancer l\'analyse maintenant</button>'
            '</form>'
        )

    # ── Top events ─────────────────────────────────────────────────────────────
    top_ev_rows = ""
    for i, (name, cnt) in enumerate((clicks.get("top_events") or []), 1):
        top_ev_rows += (
            f'<tr><td class="rk">#{i}</td>'
            f'<td class="en">{name[:55]}</td>'
            f'<td class="ec">{cnt}</td></tr>'
        )
    if not top_ev_rows:
        top_ev_rows = '<tr><td colspan="3" class="nd">Aucune donnée pour le moment</td></tr>'

    # ── Dev/auth helpers ───────────────────────────────────────────────────────
    dev_banner  = ('<div class="dev-banner">⚠️ Mode développement — données du workspace, '
                   'pas de la production.</div>') if dev_mode else ""
    badge_html  = f'<span class="badge">🔐 {user_name}</span>' if (not dev_mode and user_name) else ""
    logout_html = '<a href="/admin/logout" class="logout-btn">Déconnexion</a>' if not dev_mode else ""

    # ── Chart JS vars (built as plain f-strings to avoid brace-escaping in the big return) ──
    chart_vars = f"var lbs={chart_lbs},vs={chart_v},us={chart_u};"
    refs_vars  = f"var rl={refs_lj},rv={refs_vj},rc={refs_cj};" if has_refs else ""
    refs_init  = (
        "var rctx=document.getElementById('refChart');"
        "if(rctx){new Chart(rctx,{type:'doughnut',"
        "data:{labels:rl,datasets:[{data:rv,backgroundColor:rc,borderWidth:2,"
        "borderColor:'#fff',hoverOffset:5}]},"
        "options:{responsive:true,maintainAspectRatio:false,cutout:'65%',"
        "plugins:{legend:{display:false},"
        "tooltip:{callbacks:{label:function(c){"
        "return' '+c.label+' : '+c.parsed+' visite'+(c.parsed>1?'s':'');}"
        "}}}}});}"
    ) if has_refs else ""

    return f"""<!doctype html>
<html lang="fr"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Dashboard · Agenda Artisans Réunion</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Inter',system-ui,sans-serif;background:#f1f5f9;color:#0f172a;font-size:14px;min-height:100vh}}
a{{text-decoration:none;color:inherit}}
/* ── Dev banner ── */
.dev-banner{{background:#fef3c7;border-bottom:2px solid #fcd34d;padding:.5rem 1.5rem;font-size:.8rem;color:#92400e;font-weight:500}}
/* ── Header ── */
.hdr{{background:linear-gradient(135deg,#0f172a 0%,#1e3a5f 100%);color:#fff;padding:1.1rem 2rem;display:flex;align-items:center;justify-content:space-between;gap:1rem}}
.hdr-l{{display:flex;flex-direction:column;gap:.15rem}}
.hdr-title{{font-size:1rem;font-weight:700;letter-spacing:-.01em}}
.hdr-sub{{font-size:.72rem;color:#94a3b8}}
.hdr-r{{display:flex;align-items:center;gap:.6rem}}
.badge{{background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.2);color:#e2e8f0;font-size:.73rem;font-weight:500;padding:.3rem .65rem;border-radius:6px}}
.logout-btn{{background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.18);color:#94a3b8;font-size:.73rem;padding:.3rem .65rem;border-radius:6px;cursor:pointer;transition:.15s}}
.logout-btn:hover{{background:rgba(255,255,255,.18);color:#fff}}
/* ── Layout ── */
main{{max-width:1060px;margin:1.75rem auto;padding:0 1.25rem;display:flex;flex-direction:column;gap:1.1rem}}
/* ── KPI strip ── */
.kpi-strip{{display:grid;grid-template-columns:repeat(5,1fr);gap:.85rem}}
.kpi{{background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:1rem 1.1rem;box-shadow:0 1px 3px rgba(0,0,0,.05)}}
.kpi-val{{font-size:1.75rem;font-weight:700;line-height:1;letter-spacing:-.03em}}
.kpi-lbl{{font-size:.68rem;color:#64748b;margin-top:.3rem;font-weight:500;text-transform:uppercase;letter-spacing:.05em}}
.c-blue{{color:#2563eb}} .c-green{{color:#059669}} .c-purple{{color:#7c3aed}}
/* ── Cards ── */
.card{{background:#fff;border:1px solid #e2e8f0;border-radius:14px;padding:1.4rem 1.5rem;box-shadow:0 1px 3px rgba(0,0,0,.05),0 4px 16px rgba(0,0,0,.03)}}
.card-h{{font-size:.7rem;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:#64748b;margin-bottom:1.1rem;display:flex;align-items:center;gap:.45rem}}
/* ── Two-col grid ── */
.g2{{display:grid;grid-template-columns:1fr 1fr;gap:1.1rem}}
/* ── Charts ── */
.chart-wrap{{position:relative;height:195px;width:100%}}
/* ── Ref legend ── */
.ref-leg{{display:flex;flex-direction:column;gap:.4rem;margin-top:.75rem}}
.ref-row{{display:flex;align-items:center;gap:.55rem}}
.ref-dot{{width:9px;height:9px;border-radius:50%;flex-shrink:0}}
.ref-lbl{{font-size:.8rem;color:#374151;flex:1}}
.ref-cnt{{font-size:.8rem;font-weight:600;color:#0f172a}}
.no-chart{{display:flex;align-items:center;justify-content:center;height:195px;color:#94a3b8;font-size:.82rem;font-style:italic}}
/* ── Interactions ── */
.int-row{{display:grid;grid-template-columns:repeat(3,1fr);gap:.85rem;margin-bottom:1.1rem}}
.int-kpi{{background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:.85rem 1rem;text-align:center}}
.int-val{{font-size:1.5rem;font-weight:700}}
.int-lbl{{font-size:.68rem;color:#64748b;margin-top:.2rem;font-weight:500;text-transform:uppercase;letter-spacing:.04em}}
/* ── Table ── */
.ev-tbl{{width:100%;border-collapse:collapse;font-size:.82rem}}
.ev-tbl th{{text-align:left;padding:.45rem .6rem;border-bottom:2px solid #e2e8f0;color:#64748b;font-size:.68rem;font-weight:600;text-transform:uppercase;letter-spacing:.05em}}
.ev-tbl td{{padding:.4rem .6rem;border-bottom:1px solid #f1f5f9;color:#374151}}
.ev-tbl tr:last-child td{{border-bottom:none}}
.ev-tbl tr:hover td{{background:#f8fafc}}
td.rk{{color:#94a3b8;font-weight:600;width:34px;font-size:.73rem}}
td.en{{max-width:400px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
td.ec{{text-align:right;font-weight:700;color:#0f172a;width:55px}}
td.nd{{text-align:center;color:#94a3b8;font-style:italic;padding:1.2rem;font-size:.82rem}}
/* ── Chatbot layout ── */
.cb-split{{display:grid;grid-template-columns:155px 1fr;gap:1.4rem;align-items:start}}
.cb-kpis{{display:flex;flex-direction:column;gap:.75rem}}
/* ── Themes ── */
.th-meta{{font-size:.75rem;color:#64748b;margin-bottom:.9rem;font-style:italic}}
.th-list{{display:flex;flex-direction:column;gap:.65rem}}
.ti{{background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:.7rem .9rem}}
.ti-row{{display:flex;align-items:center;justify-content:space-between;margin-bottom:.3rem}}
.ti-name{{font-weight:600;font-size:.875rem;color:#0f172a}}
.ti-badge{{font-size:.68rem;font-weight:600;color:#2563eb;background:#eff6ff;padding:.15rem .45rem;border-radius:20px}}
.ti-bar{{height:4px;background:#e2e8f0;border-radius:4px;overflow:hidden;margin-bottom:.3rem}}
.ti-fill{{height:100%;background:linear-gradient(90deg,#2563eb,#7c3aed);border-radius:4px}}
.ti-ex{{font-size:.73rem;color:#64748b;font-style:italic}}
/* ── Empty state ── */
.empty-st{{display:flex;flex-direction:column;align-items:center;padding:1.5rem;text-align:center;color:#64748b;gap:.35rem}}
.empty-st span:first-child{{font-size:1.75rem}}
.empty-st p{{font-size:.85rem;font-weight:500;color:#374151}}
.empty-sub{{font-size:.77rem}}
/* ── Flash ── */
.flash{{border-radius:10px;padding:.7rem 1rem;font-size:.83rem;font-weight:500;margin-bottom:.2rem}}
.flash-ok{{background:#dcfce7;color:#15803d;border:1px solid #bbf7d0}}
.flash-err{{background:#fee2e2;color:#b91c1c;border:1px solid #fecaca}}
/* ── Propositions ── */
.prop-summary{{display:flex;flex-wrap:wrap;gap:.4rem .55rem;align-items:center;margin-bottom:1rem}}
.prop-cnt-pub{{background:#dcfce7;color:#15803d;padding:.2rem .65rem;border-radius:20px;font-size:.75rem;font-weight:700}}
.prop-cnt-chk{{background:#fef3c7;color:#b45309;padding:.2rem .65rem;border-radius:20px;font-size:.75rem;font-weight:700}}
.prop-from{{font-weight:400;color:#94a3b8;margin-left:.35rem;font-size:.68rem;text-transform:none;letter-spacing:0}}
.prop-list{{display:flex;flex-direction:column;gap:.7rem}}
.prop-card{{border:1px solid #e2e8f0;border-radius:10px;padding:.85rem 1rem;background:#f8fafc;transition:border-color .15s}}
.prop-card:hover{{border-color:#cbd5e1}}
.prop-top{{display:flex;align-items:flex-start;gap:.55rem;margin-bottom:.4rem;flex-wrap:wrap}}
.prop-name{{font-weight:600;font-size:.88rem;color:#0f172a;flex:1;min-width:0;line-height:1.3}}
.conf-badge{{font-size:.67rem;font-weight:700;padding:.18rem .5rem;border-radius:20px;white-space:nowrap;flex-shrink:0;margin-top:.1rem}}
.conf-vert{{background:#dcfce7;color:#15803d}}
.conf-amb{{background:#fef3c7;color:#b45309}}
.conf-grey{{background:#f1f5f9;color:#64748b}}
.prop-meta{{display:flex;flex-wrap:wrap;gap:.3rem .75rem;margin-bottom:.4rem}}
.prop-meta-item{{font-size:.74rem;color:#475569}}
.prop-notes{{font-size:.74rem;color:#64748b;font-style:italic;margin-bottom:.45rem;background:#f1f5f9;border-radius:6px;padding:.35rem .6rem;line-height:1.45}}
.prop-foot{{display:flex;align-items:center;justify-content:space-between;gap:.6rem;flex-wrap:wrap;margin-top:.5rem;padding-top:.5rem;border-top:1px solid #e2e8f0}}
.prop-src{{font-size:.74rem;color:#2563eb;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:300px;flex:1;min-width:0}}
.prop-src:hover{{text-decoration:underline}}
.prop-actions{{display:flex;gap:.4rem;flex-shrink:0}}
.prop-form{{display:inline}}
.btn-prop{{border:none;border-radius:6px;padding:.32rem .75rem;font-size:.76rem;font-weight:600;cursor:pointer;transition:background .15s;white-space:nowrap}}
.btn-pub{{background:#2563eb;color:#fff}}.btn-pub:hover{{background:#1d4ed8}}
.btn-rej{{background:#f1f5f9;color:#64748b;border:1px solid #e2e8f0}}.btn-rej:hover{{background:#e2e8f0;color:#374151}}
.prop-complete{{display:flex;gap:.4rem;margin:.45rem 0;flex-wrap:wrap}}
.inp-comp{{flex:1;min-width:200px;border:1px solid #e2e8f0;border-radius:6px;padding:.32rem .6rem;font-size:.76rem;color:#0f172a}}
.inp-comp:focus{{outline:none;border-color:#2563eb}}
.btn-comp{{background:#f0f9ff;color:#0369a1;border:1px solid #bae6fd}}.btn-comp:hover{{background:#e0f2fe}}
.prop-report{{font-size:.74rem;color:#92400e;background:#fffbeb;border:1px solid #fde68a;border-radius:6px;padding:.4rem .6rem;margin:.45rem 0;line-height:1.45}}
.prop-running{{font-size:.74rem;color:#0369a1;background:#f0f9ff;border:1px solid #bae6fd;border-radius:6px;padding:.4rem .6rem;margin:.45rem 0}}
/* ── Misc ── */
.hint-xs{{font-size:.72rem;color:#94a3b8;margin-top:.9rem}}
footer{{text-align:center;font-size:.7rem;color:#94a3b8;padding:2rem 0 1.5rem}}
@media(max-width:768px){{
  .kpi-strip{{grid-template-columns:repeat(2,1fr)}}
  .g2,.int-row,.cb-split{{grid-template-columns:1fr}}
  .prop-foot{{flex-direction:column;align-items:flex-start}}
  .prop-src{{max-width:100%;white-space:normal;word-break:break-all}}
  .prop-actions{{width:100%;display:flex;gap:.4rem}}
  .btn-prop{{flex:1;text-align:center;padding:.45rem .5rem}}
}}
</style></head>
<body>
{dev_banner}{flash_html}
<header class="hdr">
  <div class="hdr-l">
    <div class="hdr-title">📊 Dashboard — Agenda Artisans Réunion</div>
    <div class="hdr-sub">Généré le {now_str}</div>
  </div>
  <div class="hdr-r">{badge_html}{logout_html}</div>
</header>

<main>

<div class="kpi-strip">
  <div class="kpi"><div class="kpi-val c-blue">{traffic["last7_v"]}</div><div class="kpi-lbl">Visites · 7 jours</div></div>
  <div class="kpi"><div class="kpi-val">{traffic["last7_u"]}</div><div class="kpi-lbl">Uniques · 7 jours</div></div>
  <div class="kpi"><div class="kpi-val c-blue">{traffic["last30_v"]}</div><div class="kpi-lbl">Visites · 30 jours</div></div>
  <div class="kpi"><div class="kpi-val">{traffic["last30_u"]}</div><div class="kpi-lbl">Uniques · 30 jours</div></div>
  <div class="kpi"><div class="kpi-val c-green">{traffic["total_v"]}</div><div class="kpi-lbl">Total historique</div></div>
</div>

<div class="g2">
  <div class="card">
    <div class="card-h">📈 Trafic — 14 derniers jours</div>
    <div class="chart-wrap"><canvas id="trafficChart"></canvas></div>
    <p class="hint-xs" style="margin-top:.6rem">Barres = visites totales · Ligne = visiteurs uniques (IP hachée)</p>
  </div>
  <div class="card">
    <div class="card-h">🔍 Sources de trafic · 30 j.</div>
    <div class="chart-wrap">{refs_canvas}</div>
    {ref_legend}
  </div>
</div>

<div class="card">
  <div class="card-h">🖱️ Interactions · 30 jours</div>
  <div class="int-row">
    <div class="int-kpi"><div class="int-val c-purple">{clicks["chatbot_open"]}</div><div class="int-lbl">Ouvertures chatbot</div></div>
    <div class="int-kpi"><div class="int-val c-blue">{clicks["event_view"]}</div><div class="int-lbl">Fiches consultées</div></div>
    <div class="int-kpi"><div class="int-val c-green">{clicks["candidater"]}</div><div class="int-lbl">Clics « Écrire »</div></div>
  </div>
  <div class="card-h" style="margin-bottom:.75rem">🏆 Événements les plus consultés</div>
  <table class="ev-tbl">
    <thead><tr><th></th><th>Événement</th><th style="text-align:right">Vues</th></tr></thead>
    <tbody>{top_ev_rows}</tbody>
  </table>
</div>

{proposals_html}
{org_subs_html}

<div class="card">
  <div class="card-h">💬 Assistant « Le ti artisan futé »</div>
  <div class="cb-split">
    <div class="cb-kpis">
      <div class="kpi"><div class="kpi-val c-purple">{q_stats["last30"]}</div><div class="kpi-lbl">Questions · 30 j.</div></div>
      <div class="kpi"><div class="kpi-val">{q_stats["total"]}</div><div class="kpi-lbl">Total historique</div></div>
    </div>
    <div>
      <div class="card-h" style="margin-bottom:.75rem">🏷️ Thèmes récurrents</div>
      {themes_body}
    </div>
  </div>
</div>

</main>
<footer>Dashboard privé · Agenda des Exposants — Artisans de La Réunion</footer>

<script>
{chart_vars}
{refs_vars}
(function(){{
  Chart.defaults.font.family="'Inter',system-ui,sans-serif";
  Chart.defaults.font.size=12;
  var ctx=document.getElementById('trafficChart');
  if(ctx){{
    new Chart(ctx,{{
      data:{{labels:lbs,datasets:[
        {{type:'bar',label:'Visites',data:vs,backgroundColor:'rgba(37,99,235,.1)',
          borderColor:'rgba(37,99,235,.55)',borderWidth:1.5,borderRadius:4,order:2}},
        {{type:'line',label:'Uniques',data:us,borderColor:'#059669',
          backgroundColor:'rgba(5,150,105,.07)',borderWidth:2,pointRadius:3,
          pointBackgroundColor:'#059669',tension:.35,fill:true,order:1}}
      ]}},
      options:{{
        responsive:true,maintainAspectRatio:false,
        interaction:{{mode:'index',intersect:false}},
        plugins:{{
          legend:{{position:'top',labels:{{boxWidth:10,padding:14,usePointStyle:true}}}},
          tooltip:{{callbacks:{{label:function(c){{return' '+c.dataset.label+' : '+c.parsed.y;}}}}}}
        }},
        scales:{{
          x:{{grid:{{display:false}},ticks:{{maxRotation:0}}}},
          y:{{beginAtZero:true,grid:{{color:'rgba(0,0,0,.04)'}},ticks:{{precision:0}}}}
        }}
      }}
    }});
  }}
  {refs_init}
}})();
</script>
</body></html>"""


class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # Masquer les requêtes GET/HEAD habituelles pour garder les logs lisibles
        if args and str(args[1]) not in ("200", "304"):
            log.info(fmt, *args)

    def do_GET(self):
        if self.path == "/health":
            git_ok = _git_available()
            status = {
                "status": "ok" if git_ok else "degraded",
                "git_available": git_ok,
                "sync_available": git_ok,
            }
            body = json.dumps(status).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.split("?")[0] in ("/organisateurs", "/organisateurs/"):
            self._handle_org_page()
        elif self.path == "/admin/contacts.csv":
            self._handle_contacts_csv()
        elif self.path.split("?")[0] in ("/admin", "/admin/"):
            self._handle_admin()
        elif self.path == "/admin/logout":
            self.send_response(302)
            self.send_header("Location", "/admin")
            self.send_header(
                "Set-Cookie",
                f"{_SESSION_COOKIE}=; Path=/admin; HttpOnly; Secure; "
                "SameSite=Strict; Max-Age=0"
            )
            self.end_headers()
        elif self.path.startswith(("/data", "/.git", "/scripts")):
            # Fichiers internes (soumissions, contacts privés, dépôt git) —
            # jamais servis publiquement.
            self.send_response(404)
            self.end_headers()
        else:
            # Enregistre les visites du site public (GET classiques uniquement)
            if not self.path.startswith(("/sync", "/chat", "/health", "/admin", "/track")):
                ip       = (self.headers.get("X-Forwarded-For") or self.client_address[0]).split(",")[0].strip()
                referrer = self.headers.get("Referer", "")
                threading.Thread(target=_record_visit, args=(ip, referrer), daemon=True).start()
            super().do_GET()

    def _json(self, code: int, data: dict) -> None:
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_chat(self) -> None:
        ip = (self.headers.get("X-Forwarded-For") or self.client_address[0]).split(",")[0].strip()
        if not _check_rate(ip):
            self._json(429, {"error": "Limite atteinte. Maximum 20 messages par heure."})
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, ValueError):
            self._json(400, {"error": "Requête invalide."})
            return
        user_msg = str(body.get("message", "")).strip()[:1000]
        history = [
            {"role": h["role"], "content": str(h["content"])[:600]}
            for h in body.get("history", [])[-8:]
            if h.get("role") in ("user", "assistant")
        ]
        if not user_msg:
            self._json(400, {"error": "Message vide."})
            return
        if _is_events_q(user_msg):
            system = _SYS_EVENTS.format(events=_load_events())
            model  = _get_model("FAST")
        else:
            system = _SYS_ADMIN
            model  = _get_model("STRONG")
        # Enregistrement anonyme de la question pour les statistiques
        threading.Thread(target=_record_question, args=(user_msg,), daemon=True).start()
        reply = _claude(model, system, history + [{"role": "user", "content": user_msg}])
        self._json(200, {"reply": reply})

    def _handle_admin(self) -> None:
        """Sert la page de statistiques.

        - Workspace dev (REPLIT_DEPLOYMENT absent) : accès direct avec bannière.
        - Production : vérifie le cookie de session posé par POST /admin/login.
        """
        is_deployed = bool(os.environ.get("REPLIT_DEPLOYMENT"))
        dev_mode    = not is_deployed

        if is_deployed:
            token    = _get_session_cookie(self.headers)
            username = _verify_session_token(token) if token else None

            if not username:
                body = _render_auth_required().encode("utf-8")
                self.send_response(401)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                log.info("Admin : accès refusé (cookie %s).",
                         "absent" if not token else "invalide/expiré")
                return
        else:
            username = "dev"

        # Flash message depuis query string (?pub=ok ou ?err=<message>)
        qs     = urllib.parse.parse_qs(self.path.partition("?")[2])
        flash  = ""
        if "pub" in qs:
            flash = "ok:✅ Événement publié et pushé sur GitHub."
        elif "comp" in qs:
            flash = "ok:🔎 Vérification IA lancée — rechargez la page dans ~1 minute."
        elif "err" in qs:
            detail = html.escape(urllib.parse.unquote(qs["err"][0]))
            flash  = f"err:❌ Erreur : {detail}"

        body = _render_stats_page(dev_mode=dev_mode, user_name=username, flash=flash).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, no-cache")
        self.end_headers()
        self.wfile.write(body)
        log.info("Admin : accès accordé (user=%r).", username)

    def _handle_admin_login(self) -> None:
        """Vérifie le mot de passe soumis via POST /admin/login.

        Si correct → pose un cookie de session signé et redirige vers /admin.
        Si incorrect → re-affiche le formulaire avec un message d'erreur.
        """
        length   = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(length).decode("utf-8", errors="replace")
        params   = urllib.parse.parse_qs(raw_body)
        password = params.get("password", [""])[0]

        admin_pw = os.environ.get("ADMIN_PASSWORD", "")
        if not admin_pw:
            log.error("ADMIN_PASSWORD n'est pas défini — connexion impossible.")
            body = _render_auth_required(error="Configuration manquante côté serveur.").encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if not hmac.compare_digest(password, admin_pw):
            log.warning("Admin : tentative de connexion échouée.")
            body = _render_auth_required(error="Mot de passe incorrect.").encode("utf-8")
            self.send_response(401)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        token = _make_session_token("admin")
        log.info("Admin : session créée.")
        self.send_response(302)
        self.send_header("Location", "/admin")
        self.send_header(
            "Set-Cookie",
            f"{_SESSION_COOKIE}={token}; Path=/admin; HttpOnly; Secure; "
            f"SameSite=Strict; Max-Age={_SESSION_TTL}"
        )
        self.end_headers()

    def _redirect_admin(self, qs: str = "") -> None:
        """Redirige vers /admin (avec query string optionnel pour flash)."""
        loc = f"/admin?{qs}" if qs else "/admin"
        self.send_response(302)
        self.send_header("Location", loc)
        self.end_headers()

    def _admin_authorized(self) -> bool:
        """Vrai si dev_mode ou session admin valide (même règle que /admin)."""
        if not os.environ.get("REPLIT_DEPLOYMENT"):
            return True
        token = _get_session_cookie(self.headers)
        return bool(token and _verify_session_token(token))

    def _client_ip(self) -> str:
        return (self.headers.get("X-Forwarded-For")
                or self.client_address[0]).split(",")[0].strip()

    # ── Soumissions organisateurs ────────────────────────────────────────

    def _handle_org_page(self) -> None:
        """GET /organisateurs — page publique du formulaire."""
        qs = urllib.parse.parse_qs(self.path.partition("?")[2])
        flash = "ok" if "ok" in qs else ("rate" if "rate" in qs else "")
        body = _render_organisateurs_page(flash=flash).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _handle_org_submit(self) -> None:
        """POST /organisateurs — enregistre une soumission (file de relecture)."""
        length   = int(self.headers.get("Content-Length", 0))
        if length > 50_000:
            self.send_response(413)
            self.end_headers()
            return
        raw_body = self.rfile.read(length).decode("utf-8", errors="replace")
        params   = {k: v[0].strip() for k, v in urllib.parse.parse_qs(raw_body).items()}

        # Honeypot : rejet silencieux (on affiche le succès pour ne rien révéler)
        if params.get("website"):
            log.info("Soumission organisateur ignorée (honeypot rempli).")
            self._redirect("/organisateurs?ok=1")
            return

        if not _check_org_rate(self._client_ip()):
            self._redirect("/organisateurs?rate=1")
            return

        # Type : liste ou champ libre « Autre »
        type_choice = params.get("type_choice", "")
        type_autre  = params.get("type_autre", "")[:60]
        ev_type = type_autre if (type_choice == "Autre" or type_autre) else type_choice

        fields = {
            "name":  params.get("name", "")[:120],
            "zone":  params.get("zone", ""),
            "type":  ev_type[:60],
            "org":   params.get("org", "")[:120],
            "place": params.get("place", "")[:160],
            "when":  params.get("when", "")[:160],
            "deadline": params.get("deadline", "")[:160],
            "apply": params.get("apply", "")[:600],
            "email": params.get("email", "")[:120],
            "phone": params.get("phone", "")[:40],
            "social": params.get("social", "")[:120],
            "desc":  params.get("desc", "")[:600],
        }
        links = _parse_links(params.get("links", "")[:2000])
        submitter_name  = params.get("submitter_name", "")[:120]
        submitter_phone = params.get("submitter_phone", "")[:40]

        errors = []
        for label, cond in [
            ("le nom de l'événement", fields["name"]),
            ("la zone", fields["zone"] in _ZONES),
            ("le type d'événement", fields["type"]),
            ("l'organisateur", fields["org"]),
            ("le lieu", fields["place"]),
            ("la date ou période", fields["when"]),
            ("au moins un lien valide", links),
            ("comment candidater", fields["apply"]),
            ("un email de contact valide",
             re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", fields["email"] or "")),
            ("la description", fields["desc"]),
            ("votre nom", submitter_name),
            ("votre téléphone", submitter_phone),
        ]:
            if not cond:
                errors.append(label)
        if errors:
            form = dict(params)
            flash = "err:Champs manquants ou invalides : " + ", ".join(errors) + "."
            body = _render_organisateurs_page(flash=flash, form=form).encode("utf-8")
            self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        sub = {
            "id": uuid.uuid4().hex[:12],
            "ts": datetime.datetime.now().isoformat(timespec="seconds"),
            "status": "pending",
            "fields": fields,
            "links": links,
            "submitter_name": submitter_name,
            "submitter_phone": submitter_phone,
            "reviewer_note": "",
        }
        with _submissions_lock:
            subs = _load_json_list(_SUBMISSIONS_FILE)
            # Garde anti double-envoi : même nom + même email déjà soumis
            # dans les 5 dernières minutes → on ignore le doublon (succès
            # silencieux, la 1re soumission est déjà enregistrée).
            now = datetime.datetime.now()
            for prev in subs:
                if (prev.get("fields", {}).get("name", "").strip().lower()
                        == fields["name"].strip().lower()
                        and prev.get("fields", {}).get("email", "").strip().lower()
                        == fields["email"].strip().lower()):
                    try:
                        age = (now - datetime.datetime.fromisoformat(prev["ts"])).total_seconds()
                    except (ValueError, KeyError):
                        continue
                    if age < 300:
                        log.info("Soumission organisateur ignorée (doublon <5 min) : %s",
                                 fields["name"])
                        self._redirect("/organisateurs?ok=1")
                        return
            subs.append(sub)
            _save_json_list(_SUBMISSIONS_FILE, subs)
        log.info("Nouvelle soumission organisateur : %s (%s)",
                 fields["name"], sub["id"])

        # Durabilité + notification immédiate — en arrière-plan (serveur mono-thread)
        def _notify():
            _push_org_files(["data/organizer_submissions.json"])
            _send_email(
                f"📨 Nouvelle soumission organisateur : {fields['name']}",
                "Une nouvelle proposition d'événement vient d'arriver sur "
                "/organisateurs :\n\n"
                + "\n".join(f"- {k} : {v}" for k, v in fields.items() if v)
                + "\n- liens : " + " ; ".join(links)
                + f"\n- soumis par : {submitter_name} ({submitter_phone})"
                + "\n\nÀ relire dans /admin (section Soumissions organisateurs).",
                _ORG_OWNER_EMAIL,
            )
        threading.Thread(target=_notify, daemon=True).start()
        self._redirect("/organisateurs?ok=1")

    def _redirect(self, loc: str) -> None:
        self.send_response(303)
        self.send_header("Location", loc)
        self.end_headers()

    def _find_submission(self, sub_id: str, subs: list):
        return next((s for s in subs
                     if s.get("id") == sub_id and s.get("status") == "pending"), None)

    def _handle_org_approve(self) -> None:
        """POST /admin/org-approve — publie via le MÊME chemin que les candidats IA."""
        if not self._admin_authorized():
            self.send_response(403)
            self.end_headers()
            return
        length   = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(length).decode("utf-8", errors="replace")
        params   = urllib.parse.parse_qs(raw_body)
        sub_id   = params.get("id", [""])[0]
        note     = params.get("note", [""])[0][:300]

        with _submissions_lock:
            subs = _load_json_list(_SUBMISSIONS_FILE)
            sub  = self._find_submission(sub_id, subs)
        if not sub:
            self._redirect_admin(
                "err=" + urllib.parse.quote("Soumission introuvable ou déjà traitée.", safe=""))
            return

        ok, msg = _publish_event_to_repo(_submission_to_event(sub))
        if not ok:
            log.error("Publication soumission %s échouée : %s", sub_id, msg)
            self._redirect_admin("err=" + urllib.parse.quote(msg, safe=""))
            return

        with _submissions_lock:
            subs = _load_json_list(_SUBMISSIONS_FILE)
            sub  = next((s for s in subs if s.get("id") == sub_id), sub)
            sub["status"] = "approved"
            sub["reviewer_note"] = note
            _save_json_list(_SUBMISSIONS_FILE, subs)
            paths, _ = _update_org_contact(sub)

        ev_name = sub["fields"].get("name", "")
        org_email = sub["fields"].get("email", "")

        def _post_approve():
            _push_org_files(paths)
            if org_email:
                _send_email(
                    f"✅ Votre événement « {ev_name} » est en ligne",
                    "Bonjour,\n\n"
                    f"Bonne nouvelle : votre événement « {ev_name} » a été validé "
                    "et figure désormais sur le Radar des marchés de La Réunion :\n"
                    "https://radar.fhservices.re\n\n"
                    "Merci d'avoir pris le temps de nous le proposer — n'hésitez "
                    "pas à soumettre vos prochains événements sur "
                    "https://radar.fhservices.re/organisateurs\n\n"
                    "Bien cordialement,\nRadar Marchés Réunion",
                    org_email,
                )
        threading.Thread(target=_post_approve, daemon=True).start()
        self._redirect_admin("pub=ok")

    def _handle_org_reject(self) -> None:
        """POST /admin/org-reject — marque la soumission rejetée (avec note)."""
        if not self._admin_authorized():
            self.send_response(403)
            self.end_headers()
            return
        length   = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(length).decode("utf-8", errors="replace")
        params   = urllib.parse.parse_qs(raw_body)
        sub_id   = params.get("id", [""])[0]
        note     = params.get("note", [""])[0][:300]

        with _submissions_lock:
            subs = _load_json_list(_SUBMISSIONS_FILE)
            sub  = self._find_submission(sub_id, subs)
            if sub:
                sub["status"] = "rejected"
                sub["reviewer_note"] = note
                _save_json_list(_SUBMISSIONS_FILE, subs)
        if sub:
            threading.Thread(
                target=_push_org_files,
                args=(["data/organizer_submissions.json"],), daemon=True).start()
        self._redirect_admin()

    def _handle_contacts_csv(self) -> None:
        """GET /admin/contacts.csv — export privé de l'annuaire de contacts."""
        if not self._admin_authorized():
            self.send_response(403)
            self.end_headers()
            return
        import csv
        import io
        contacts = _load_json_list(_ORG_CONTACTS_FILE)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["email", "nom", "telephone", "reseaux_sociaux",
                    "premier_contact", "dernier_contact",
                    "evenements_soumis", "notes_internes"])
        for c in contacts:
            w.writerow([c.get("email", ""), c.get("name", ""), c.get("phone", ""),
                        c.get("social", ""), c.get("first_contact", ""),
                        c.get("last_contact", ""), c.get("events_submitted", 0),
                        c.get("notes", "")])
        body = buf.getvalue().encode("utf-8-sig")
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition",
                         'attachment; filename="contacts_organisateurs.csv"')
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _handle_publish(self) -> None:
        """POST /admin/publish — publie un candidat Vérifié dans events.json."""
        dev_mode = not os.environ.get("REPLIT_DEPLOYMENT")
        if not dev_mode:
            token    = _get_session_cookie(self.headers)
            username = _verify_session_token(token) if token else None
            if not username:
                self.send_response(403)
                self.end_headers()
                return

        length   = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(length).decode("utf-8", errors="replace")
        params   = urllib.parse.parse_qs(raw_body)
        key      = params.get("key", [""])[0]

        if not key:
            self.send_response(400)
            self.end_headers()
            return

        _, candidates = _load_latest_proposal()
        candidate = next((c for c in candidates if _candidate_key(c) == key), None)
        if candidate and not candidate.get("event"):
            comp = _load_completions().get(key)
            if comp and comp.get("status") == "done" and comp.get("event"):
                candidate["event"] = comp["event"]
        if not candidate or not candidate.get("event"):
            self._redirect_admin("err=Candidat+introuvable+ou+sans+fiche+complète.")
            return

        ok, msg = _publish_event_to_repo(candidate["event"])
        if ok:
            _save_decision(key, "published")
            _push_decisions()
            self._redirect_admin("pub=ok")
        else:
            log.error("Publication échouée : %s", msg)
            self._redirect_admin("err=" + urllib.parse.quote(msg, safe=""))

    def _handle_reject(self) -> None:
        """POST /admin/reject — rejette un candidat (le masque de la file)."""
        dev_mode = not os.environ.get("REPLIT_DEPLOYMENT")
        if not dev_mode:
            token    = _get_session_cookie(self.headers)
            username = _verify_session_token(token) if token else None
            if not username:
                self.send_response(403)
                self.end_headers()
                return

        length   = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(length).decode("utf-8", errors="replace")
        params   = urllib.parse.parse_qs(raw_body)
        key      = params.get("key", [""])[0]

        if key:
            _save_decision(key, "rejected")
            _push_decisions()
        self._redirect_admin()

    def _handle_complete(self) -> None:
        """POST /admin/complete — lance la vérification/complétion IA d'un candidat."""
        dev_mode = not os.environ.get("REPLIT_DEPLOYMENT")
        if not dev_mode:
            token    = _get_session_cookie(self.headers)
            username = _verify_session_token(token) if token else None
            if not username:
                self.send_response(403)
                self.end_headers()
                return

        length   = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(length).decode("utf-8", errors="replace")
        params   = urllib.parse.parse_qs(raw_body)
        key      = params.get("key", [""])[0]
        info     = params.get("info", [""])[0].strip()[:600]

        if not key:
            self.send_response(400)
            self.end_headers()
            return

        _, candidates = _load_latest_proposal()
        candidate = next((c for c in candidates if _candidate_key(c) == key), None)
        if not candidate:
            self._redirect_admin("err=Candidat+introuvable.")
            return

        _save_completion(key, {"status": "running", "report": ""})
        threading.Thread(target=_run_completion_job, args=(key, candidate, info),
                         daemon=True, name="ai-complete").start()
        self._redirect_admin("comp=run")

    def _handle_run_analysis(self) -> None:
        """Déclenche l'analyse des thèmes manuellement (POST /admin/run-analysis)."""
        dev_mode = not os.environ.get("REPLIT_DEPLOYMENT")
        if not dev_mode:
            token    = _get_session_cookie(self.headers)
            username = _verify_session_token(token) if token else None
            if not username:
                self.send_response(403)
                self.end_headers()
                return
        threading.Thread(target=_run_theme_analysis, daemon=True, name="theme-manual").start()
        self.send_response(302)
        self.send_header("Location", "/admin")
        self.end_headers()

    def _handle_track(self) -> None:
        """Reçoit un ping de tracking côté client (fire-and-forget)."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            body   = self.rfile.read(min(length, 512))
            entry  = json.loads(body)
            ev     = entry.get("e", "")[:32]
            name   = entry.get("n", "")[:80]
            if ev in ("chatbot_open", "candidater", "event_view"):
                threading.Thread(target=_record_click, args=(ev, name), daemon=True).start()
        except Exception:
            pass  # tracking silencieux — on n'interrompt jamais l'utilisateur
        self.send_response(204)
        self.end_headers()

    def do_POST(self):
        if self.path == "/track":
            self._handle_track()
            return

        if self.path == "/admin/login":
            self._handle_admin_login()
            return

        if self.path == "/admin/run-analysis":
            self._handle_run_analysis()
            return

        if self.path == "/admin/publish":
            self._handle_publish()
            return

        if self.path == "/admin/reject":
            self._handle_reject()
            return

        if self.path == "/admin/complete":
            self._handle_complete()
            return

        if self.path.split("?")[0] in ("/organisateurs", "/organisateurs/"):
            self._handle_org_submit()
            return

        if self.path == "/admin/org-approve":
            self._handle_org_approve()
            return

        if self.path == "/admin/org-reject":
            self._handle_org_reject()
            return

        if self.path == "/chat":
            self._handle_chat()
            return

        if self.path != "/sync":
            self.send_response(404)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", 0))
        payload = self.rfile.read(length)
        sig = self.headers.get("X-Hub-Signature-256", "")

        if not verify_signature(payload, sig):
            log.warning("Signature webhook invalide — requête rejetée.")
            self.send_response(403)
            self.end_headers()
            self.wfile.write(b"Forbidden")
            return

        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Bad Request")
            return

        ref = data.get("ref", "")
        if ref != f"refs/heads/{BRANCH}":
            log.info("Push sur '%s' ignoré (seul '%s' est surveillé).", ref, BRANCH)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Ignored")
            return

        log.info("Push détecté sur %s — git pull en cours…", BRANCH)
        threading.Thread(target=git_pull, daemon=True).start()

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    server = http.server.HTTPServer(("0.0.0.0", port), Handler)
    log.info("Serveur démarré sur le port %d", port)
    log.info("Webhook disponible sur POST /sync")

    # Alerte immédiate si git est absent au démarrage
    if not _git_available():
        log.warning(
            "ALERTE MODE DÉGRADÉ : le serveur démarre SANS dépôt git. "
            "GitHub était inaccessible au démarrage (scripts/start.sh n'a pas pu initialiser git). "
            "Le endpoint /sync est indisponible. "
            "Consultez GET /health pour surveiller l'état. "
            "Redémarrez le serveur via scripts/start.sh pour rétablir la synchronisation."
        )
        # Notification unique (email ou webhook) — lancée dans un thread pour ne pas bloquer le démarrage
        threading.Thread(target=_send_degraded_alert, daemon=True, name="degraded-alert").start()
        # Thread daemon : rappels périodiques tant que git reste absent
        threading.Thread(target=_periodic_git_warning, daemon=True, name="git-warn").start()
    else:
        log.info("Dépôt git détecté — endpoint /sync opérationnel.")

    if not WEBHOOK_SECRET:
        log.warning(
            "GITHUB_WEBHOOK_SECRET non défini ! "
            "Définissez ce secret Replit pour sécuriser le webhook."
        )

    # Vérification périodique des modèles Claude (démarrage immédiat + toutes les 24 h)
    threading.Thread(target=_model_check_loop, daemon=True, name="model-check").start()

    # Analyse hebdomadaire des thèmes de questions du chatbot
    threading.Thread(target=_theme_analysis_loop, daemon=True, name="theme-analysis").start()
    log.info("Page de statistiques disponible sur GET /admin (accès restreint au propriétaire Replit).")

    server.serve_forever()
