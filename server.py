#!/usr/bin/env python3
"""
Serveur statique + webhook GitHub.
Sert index.html sur le port 5000 et expose /sync pour déclencher
un git pull automatique à chaque push sur la branche main.
"""

import datetime
import hashlib
import hmac
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
import urllib.request
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


def _send_email_alert(recipient: str) -> bool:
    """Envoie une alerte par email via SMTP.
    Retourne True en cas de succès."""
    smtp_host = os.environ.get("SMTP_HOST", "localhost")
    smtp_port = int(os.environ.get("SMTP_PORT", 587))
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_password = os.environ.get("SMTP_PASSWORD", "")
    smtp_from = os.environ.get("SMTP_FROM", "noreply@localhost")

    msg = EmailMessage()
    msg["Subject"] = "⚠️ Serveur démarré en mode dégradé (git absent)"
    msg["From"] = smtp_from
    msg["To"] = recipient
    msg.set_content(_ALERT_MESSAGE)

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as server:
            server.ehlo()
            if smtp_port != 25:
                server.starttls()
                server.ehlo()
            if smtp_user and smtp_password:
                server.login(smtp_user, smtp_password)
            server.send_message(msg)
        log.info("Alerte email envoyée à %s.", recipient)
        return True
    except Exception as exc:
        log.error("Échec envoi alerte email : %s", exc)
        return False


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


# ── Analytics — statistiques du site ─────────────────────────────────────────

_DATA_DIR       = "data"
_TRAFFIC_FILE   = os.path.join(_DATA_DIR, "traffic.json")
_QUESTIONS_FILE = os.path.join(_DATA_DIR, "chat_questions.jsonl")
_THEMES_FILE    = os.path.join(_DATA_DIR, "theme_analysis.json")

_traffic_lock   = threading.Lock()
_questions_lock = threading.Lock()

# IPs uniques vues aujourd'hui (reset automatique au changement de jour)
_today_ips:      set = set()
_today_date_str: str = ""

_THEMES_INTERVAL = 7 * 24 * 3600  # analyse hebdomadaire


def _record_visit(ip: str) -> None:
    """Enregistre une visite sur le site public (thread-safe)."""
    global _today_ips, _today_date_str
    today = datetime.date.today().isoformat()
    ip_hash = hashlib.sha256(ip.encode()).hexdigest()[:16]

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
            day = data.setdefault(today, {"v": 0, "u": 0})
            day["v"] += 1
            if is_new:
                day["u"] += 1
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
        return

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
    except Exception as exc:
        log.error("_run_theme_analysis (sauvegarde) : %s", exc)


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
        _run_theme_analysis()
        time.sleep(_THEMES_INTERVAL)


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
    for i in range(30):
        d   = today - datetime.timedelta(days=i)
        key = d.isoformat()
        dd  = raw.get(key, {"v": 0, "u": 0})
        days.append({"date": key, "label": d.strftime("%-d %b"), "v": dd.get("v", 0), "u": dd.get("u", 0)})
        last30_v += dd.get("v", 0)
        last30_u += dd.get("u", 0)
        if i < 7:
            last7_v += dd.get("v", 0)
            last7_u += dd.get("u", 0)
    return {"days": days, "last7_v": last7_v, "last7_u": last7_u,
            "last30_v": last30_v, "last30_u": last30_u,
            "total_v": total_v, "total_u": total_u}


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


def _render_auth_required() -> str:
    return """<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Accès restreint</title>
<style>
  body{font-family:system-ui,sans-serif;background:#f9fafb;display:flex;
       align-items:center;justify-content:center;min-height:100vh;margin:0}
  .card{background:#fff;border:1px solid #e5e7eb;border-radius:12px;
        padding:2.5rem;max-width:420px;text-align:center;box-shadow:0 2px 8px rgba(0,0,0,.06)}
  h1{font-size:1.2rem;color:#111827;margin:0 0 .6rem}
  p{color:#6b7280;font-size:.9rem;line-height:1.6;margin:0 0 1.25rem}
  .steps{text-align:left;background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;
         padding:.9rem 1.1rem;margin:0 0 1.25rem;font-size:.875rem;color:#374151;line-height:1.8}
  .steps strong{color:#111827}
  .btn{display:inline-block;padding:.6rem 1.3rem;border-radius:8px;font-size:.875rem;
       font-weight:500;text-decoration:none;cursor:pointer;border:none;margin:.25rem}
  .btn-primary{background:#2563eb;color:#fff}
  .btn-primary:hover{background:#1d4ed8}
  .btn-reload{background:#f3f4f6;color:#374151;border:1px solid #d1d5db;display:none}
  .btn-reload:hover{background:#e5e7eb}
  .hint{font-size:.78rem;color:#9ca3af;margin-top:.75rem}
</style></head>
<body>
  <div class="card">
    <div style="font-size:2rem;margin-bottom:.75rem">🔒</div>
    <h1>Accès réservé au propriétaire</h1>
    <p>Cette page nécessite d'être connecté à Replit avec le compte propriétaire.</p>
    <div class="steps">
      <strong>Étape 1 —</strong> Cliquez sur le bouton ci-dessous pour vous connecter à Replit dans un nouvel onglet.<br>
      <strong>Étape 2 —</strong> Une fois connecté, revenez ici et cliquez sur <em>« Recharger »</em>.
    </div>
    <div>
      <button class="btn btn-primary" onclick="openLogin()">Se connecter à Replit</button>
      <button class="btn btn-reload" id="reloadBtn" onclick="location.reload()">↺ Recharger la page</button>
    </div>
    <p class="hint">Le bouton « Recharger » apparaît après avoir cliqué sur « Se connecter ».</p>
  </div>
  <script>
    function openLogin() {
      window.open('https://replit.com/login', '_blank');
      var btn = document.getElementById('reloadBtn');
      btn.style.display = 'inline-block';
      document.querySelector('.hint').textContent =
        'Connectez-vous dans le nouvel onglet, puis cliquez sur Recharger ci-dessus.';
    }
  </script>
</body></html>"""


def _render_stats_page(dev_mode: bool, user_name: str) -> str:
    traffic  = _load_traffic_stats()
    q_stats  = _load_questions_stats()
    themes   = _load_themes()
    now_str  = datetime.datetime.now().strftime("%d/%m/%Y à %H:%M")

    # Table des 30 derniers jours
    rows_html = ""
    for d in traffic["days"]:
        rows_html += (
            f'<tr><td>{d["date"]}</td>'
            f'<td class="num">{d["v"]}</td>'
            f'<td class="num">{d["u"]}</td></tr>\n'
        )

    # Thèmes
    if themes and themes.get("themes"):
        gen_at = themes.get("generated_at", "")
        try:
            gen_date = datetime.datetime.fromisoformat(gen_at).strftime("%d/%m/%Y")
        except Exception:
            gen_date = gen_at[:10] if gen_at else "—"
        themes_html = (
            f'<p class="meta">Analyse du {gen_date} &mdash; '
            f'{themes.get("total_analyzed", "?")} questions des {themes.get("period_days", 30)} derniers jours</p>'
            '<ol class="themes-list">'
        )
        for t in sorted(themes["themes"], key=lambda x: x.get("count", 0), reverse=True):
            themes_html += (
                f'<li><strong>{t.get("name","?")}</strong>'
                f' <span class="badge">{t.get("count","?")} questions</span>'
                f'<div class="example">Ex : {t.get("example","—")}</div></li>'
            )
        themes_html += "</ol>"
        themes_next = f"Prochaine analyse dans environ {round(_THEMES_INTERVAL/3600/24)} jours."
    else:
        themes_html = (
            '<p class="meta empty">Pas encore d\'analyse disponible.<br>'
            f'L\'analyse se lance automatiquement dès que 3 questions ont été posées, '
            f'puis toutes les 7 jours.</p>'
        )
        themes_next = ""

    dev_banner = ""
    if dev_mode:
        dev_banner = (
            '<div class="dev-banner">⚠️ Mode développement — '
            'REPL_OWNER non défini. En production, cette page est réservée au propriétaire Replit.</div>'
        )
    elif user_name:
        user_tag = f'<span class="user-tag">🔐 {user_name}</span>'
    else:
        user_tag = ""

    user_tag = user_tag if not dev_mode else ""

    return f"""<!doctype html>
<html lang="fr"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Statistiques — Agenda des Exposants</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:system-ui,-apple-system,sans-serif;background:#f3f4f6;color:#111827;min-height:100vh}}
  .dev-banner{{background:#fef3c7;border-bottom:1px solid #fcd34d;padding:.6rem 1.5rem;
               font-size:.85rem;color:#92400e}}
  header{{background:#fff;border-bottom:1px solid #e5e7eb;padding:1rem 1.5rem;
          display:flex;align-items:center;justify-content:space-between}}
  header h1{{font-size:1.1rem;font-weight:600;color:#111827}}
  header p{{font-size:.8rem;color:#6b7280;margin-top:.15rem}}
  .user-tag{{font-size:.8rem;background:#eff6ff;color:#1d4ed8;padding:.3rem .7rem;
             border-radius:6px;border:1px solid #bfdbfe}}
  main{{max-width:900px;margin:2rem auto;padding:0 1rem;display:grid;gap:1.5rem}}
  .card{{background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:1.5rem;
         box-shadow:0 1px 4px rgba(0,0,0,.05)}}
  .card h2{{font-size:1rem;font-weight:600;color:#111827;margin-bottom:1rem;
            display:flex;align-items:center;gap:.5rem}}
  .card h2 .icon{{font-size:1.1rem}}
  .kpi-row{{display:flex;gap:1rem;margin-bottom:1.25rem;flex-wrap:wrap}}
  .kpi{{flex:1;min-width:120px;background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;
        padding:.85rem 1rem}}
  .kpi .val{{font-size:1.6rem;font-weight:700;color:#111827;line-height:1.1}}
  .kpi .lbl{{font-size:.75rem;color:#6b7280;margin-top:.25rem}}
  .kpi.accent .val{{color:#2563eb}}
  table{{width:100%;border-collapse:collapse;font-size:.875rem}}
  thead th{{text-align:left;padding:.5rem .75rem;border-bottom:2px solid #e5e7eb;
            color:#6b7280;font-weight:500;font-size:.8rem;text-transform:uppercase;letter-spacing:.04em}}
  tbody tr:hover{{background:#f9fafb}}
  tbody td{{padding:.45rem .75rem;border-bottom:1px solid #f3f4f6;color:#374151}}
  td.num{{text-align:right;font-variant-numeric:tabular-nums;color:#111827;font-weight:500}}
  .meta{{font-size:.85rem;color:#6b7280;margin-bottom:1rem}}
  .meta.empty{{font-style:italic}}
  .themes-list{{list-style:none;display:flex;flex-direction:column;gap:.75rem}}
  .themes-list li{{background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:.75rem 1rem}}
  .themes-list li strong{{color:#111827;font-size:.95rem}}
  .badge{{display:inline-block;background:#dbeafe;color:#1e40af;font-size:.75rem;font-weight:600;
          padding:.15rem .5rem;border-radius:20px;margin-left:.5rem;vertical-align:middle}}
  .example{{font-size:.8rem;color:#6b7280;margin-top:.3rem;font-style:italic}}
  .hint{{font-size:.8rem;color:#9ca3af;margin-top:.75rem}}
  footer{{text-align:center;font-size:.75rem;color:#9ca3af;padding:2rem 0}}
</style>
</head>
<body>
{dev_banner}
<header>
  <div>
    <h1>📊 Tableau de bord — Agenda des Exposants</h1>
    <p>Généré le {now_str} (UTC+4)</p>
  </div>
  {user_tag}
</header>
<main>

  <!-- Trafic -->
  <div class="card">
    <h2><span class="icon">🌐</span> Trafic du site public</h2>
    <div class="kpi-row">
      <div class="kpi accent">
        <div class="val">{traffic["last7_v"]}</div>
        <div class="lbl">Visites — 7 derniers jours</div>
      </div>
      <div class="kpi">
        <div class="val">{traffic["last7_u"]}</div>
        <div class="lbl">Visiteurs uniques — 7 j.</div>
      </div>
      <div class="kpi accent">
        <div class="val">{traffic["last30_v"]}</div>
        <div class="lbl">Visites — 30 derniers jours</div>
      </div>
      <div class="kpi">
        <div class="val">{traffic["last30_u"]}</div>
        <div class="lbl">Visiteurs uniques — 30 j.</div>
      </div>
      <div class="kpi">
        <div class="val">{traffic["total_v"]}</div>
        <div class="lbl">Visites totales (historique)</div>
      </div>
    </div>
    <table>
      <thead>
        <tr>
          <th>Date</th>
          <th style="text-align:right">Visites</th>
          <th style="text-align:right">Visiteurs uniques</th>
        </tr>
      </thead>
      <tbody>
        {rows_html}
      </tbody>
    </table>
    <p class="hint">Les visiteurs uniques sont estimés par IP (hachée, non stockée en clair). Les chiffres couvrent les 30 derniers jours.</p>
  </div>

  <!-- Chatbot -->
  <div class="card">
    <h2><span class="icon">💬</span> Assistant « Le ti artisan futé »</h2>
    <div class="kpi-row">
      <div class="kpi accent">
        <div class="val">{q_stats["last30"]}</div>
        <div class="lbl">Questions — 30 derniers jours</div>
      </div>
      <div class="kpi">
        <div class="val">{q_stats["total"]}</div>
        <div class="lbl">Questions totales (historique)</div>
      </div>
    </div>

    <h2 style="margin-top:1.25rem"><span class="icon">🏷️</span> Thèmes récurrents</h2>
    {themes_html}
    <p class="hint">{themes_next}</p>
  </div>

</main>
<footer>Page privée — accessible uniquement via compte Replit propriétaire</footer>
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
        elif self.path in ("/admin", "/admin/"):
            self._handle_admin()
        else:
            # Enregistre les visites du site public (GET classiques uniquement)
            if not self.path.startswith(("/sync", "/chat", "/health", "/admin")):
                ip = (self.headers.get("X-Forwarded-For") or self.client_address[0]).split(",")[0].strip()
                threading.Thread(target=_record_visit, args=(ip,), daemon=True).start()
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
        """Sert la page de statistiques privée (auth Replit requise en production).

        En workspace de développement (REPLIT_DEPLOYMENT absent), le proxy Replit
        n'injecte jamais X-Replit-User-Name — l'accès est accordé directement avec
        une bannière d'avertissement.
        En production déployée (REPLIT_DEPLOYMENT présent), le header est injecté
        automatiquement par Replit pour les utilisateurs connectés.
        """
        is_deployed = bool(os.environ.get("REPLIT_DEPLOYMENT"))
        repl_owner  = os.environ.get("REPL_OWNER", "")
        user_name   = self.headers.get("X-Replit-User-Name", "")
        dev_mode    = not is_deployed  # workspace dev : pas d'injection de headers

        if is_deployed and user_name != repl_owner:
            body = _render_auth_required().encode("utf-8")
            self.send_response(401)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            log.info("Admin /admin : accès refusé (user=%r, owner=%r).", user_name, repl_owner)
            return

        body = _render_stats_page(dev_mode=dev_mode, user_name=user_name).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, no-cache")
        self.end_headers()
        self.wfile.write(body)
        log.info("Admin /admin : accès accordé (user=%r, deployed=%s).", user_name or "dev", is_deployed)

    def do_POST(self):
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
