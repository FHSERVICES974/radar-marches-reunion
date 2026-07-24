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


def _render_stats_page(dev_mode: bool, user_name: str) -> str:  # noqa: PLR0912,PLR0915
    traffic  = _load_traffic_stats()
    q_stats  = _load_questions_stats()
    themes   = _load_themes()
    clicks   = _load_clicks_stats()
    now_str  = datetime.datetime.now().strftime("%d/%m/%Y à %H:%M")

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
/* ── Misc ── */
.hint-xs{{font-size:.72rem;color:#94a3b8;margin-top:.9rem}}
footer{{text-align:center;font-size:.7rem;color:#94a3b8;padding:2rem 0 1.5rem}}
@media(max-width:768px){{
  .kpi-strip{{grid-template-columns:repeat(2,1fr)}}
  .g2,.int-row,.cb-split{{grid-template-columns:1fr}}
}}
</style></head>
<body>
{dev_banner}
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
        elif self.path in ("/admin", "/admin/"):
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

        body = _render_stats_page(dev_mode=dev_mode, user_name=username).encode("utf-8")
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
