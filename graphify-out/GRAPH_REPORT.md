# Graph Report - /Users/fhubert/Claude/radarartisans  (2026-07-27)

## Corpus Check
- 52 files · ~163,235 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 397 nodes · 670 edges · 36 communities (32 shown, 4 thin omitted)
- Extraction: 93% EXTRACTED · 7% INFERRED · 0% AMBIGUOUS · INFERRED: 48 edges (avg confidence: 0.77)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- Admin server: auth & submissions
- common.py — shared helpers
- Chat widget (Le ti artisan futé)
- server.py core (rate-limit, email, git)
- Stats persistence (Postgres migration)
- SEO foundations
- publier.py — publish pipeline
- Public site UI (event cards)
- README — architecture & incidents
- Stats v2 brief — event lifecycle
- capté ≠ publié — veille/ingest principle
- server.py: publish/retire/edit event
- build.py — template injection + JSON-LD
- Event metadata & publish dates
- Admin decisions & completions cache
- Admin dashboard sections
- Traffic tracking (referrer/visitor hash)
- Claude model auto-check
- Email/webhook alerting
- GitHub↔Replit deploy pipeline
- doc_to_text.py — document extraction
- Organiser CRM & admin briefs
- run_veille.sh / run_weekly.sh scripts
- Radar Marchés logo (image)
- Replit auth redirect bug (screenshot)
- Admin password auth (screenshot)
- git push error (screenshot)
- Per-event stats section
- Organiser submission → event conversion
- Admin stats + git error (screenshot)
- Admin stats dashboard (screenshot)
- ingest_docs.sh script
- Mobile capture (obsolete iPhone shortcut)
- start.sh — git init on boot
- post-merge.sh
- Replit Nix workspace

## God Nodes (most connected - your core abstractions)
1. `Handler` - 30 edges
2. `README — Radar Marchés (Agenda des Exposants)` - 17 edges
3. `_stats_query()` - 12 edges
4. `_render_stats_page()` - 11 edges
5. `_render_proposals_section()` - 10 edges
6. `template.html — frozen design source` - 10 edges
7. `_stats_connect()` - 9 edges
8. `Playbook de veille (veille_agent.md) — agent « cerveau »` - 9 edges
9. `main()` - 8 edges
10. `_push_runtime_file()` - 8 edges

## Surprising Connections (you probably didn't know these)
- `Radar Marches - Agenda Evenements Screenshot` --conceptually_related_to--> `Radar Marches (agenda artisans 974 project)`  [INFERRED]
  attached_assets/image_1784951363847.png → radar-marches-projet.md
- `Principe « déposé ≠ publié »` --semantically_similar_to--> `Règle absolue « capté ≠ publié »`  [INFERRED] [semantically similar]
  BRIEF_Claude_Code_formulaire_organisateurs.md → veille_agent.md
- `index.html — generated public site page` --semantically_similar_to--> `reference_dashboard-artisans-reunion.html (non-regression reference)`  [INFERRED] [semantically similar]
  index.html → reference_dashboard-artisans-reunion.html
- `Playbook d'ingestion de documents (ingest_agent.md)` --semantically_similar_to--> `Règle absolue « capté ≠ publié »`  [INFERRED] [semantically similar]
  ingest_agent.md → veille_agent.md
- `_render_org_submissions_section()` --calls--> `esc()`  [INFERRED]
  server.py → email_template.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **PostgreSQL stats persistence guarantee (never local files)** — _agents_memory_stats_postgres_stats_conventions, brief_claude_code_stats_v2_persistence_check, readme, _agents_memory_stats_postgres_daily_snapshot [INFERRED 0.85]
- **« Capté ≠ publié » human-validation governance across intake channels** — veille_agent, ingest_agent, brief_claude_code_formulaire_organisateurs, readme [INFERRED 0.85]
- **Frozen design guarantee: template.html as sole source, verified against reference/generated output** — template, reference_dashboard_artisans_reunion, index, readme_regle_or_design_fige [EXTRACTED 1.00]

## Communities (36 total, 4 thin omitted)

### Community 0 - "Admin server: auth & submissions"
Cohesion: 0.06
Nodes (45): _candidate_key(), _get_session_cookie(), Handler, _load_json_list(), _load_latest_proposal(), _push_decisions(), _push_org_files(), _push_runtime_file() (+37 more)

### Community 1 - "common.py — shared helpers"
Cohesion: 0.08
Nodes (37): backup_events(), event_key(), git(), is_git_repo(), load_json(), macos_notify(), norm(), parse_dates_from_text() (+29 more)

### Community 2 - "Chat widget (Le ti artisan futé)"
Cohesion: 0.08
Nodes (23): _check_rate(), _claude(), _fetch_page_text(), _get_model(), _is_events_q(), _load_events(), _push_completions(), Enregistre une question du chatbot (append JSONL, thread-safe). (+15 more)

### Community 3 - "server.py core (rate-limit, email, git)"
Cohesion: 0.12
Nodes (16): _check_org_rate(), _email_card(), _email_html(), git_pull(), _invalidate_events_cache(), _make_session_token(), _parse_links(), _parse_replit_auth_response() (+8 more)

### Community 4 - "Stats persistence (Postgres migration)"
Cohesion: 0.14
Nodes (17): _import_legacy_stats(), _load_clicks_stats(), Statistiques d'interactions des 30 derniers jours (PostgreSQL)., Rapport imprimable une page pour UN événement (offre visibilité) :     totaux de, Lecture ponctuelle (connexion courte). Lève en cas d'échec — loggé par l'appelan, Thread unique d'écriture : consomme la file et insère en base.     Reconnexion a, Purge quotidienne : supprime tout ce qui dépasse 24 mois (conformité)., Reprise unique des anciens fichiers locaux (traffic.json / clicks.jsonl)     ver (+9 more)

### Community 5 - "SEO foundations"
Cohesion: 0.19
Nodes (15): Brief SEO & indexation Google, Canonical domain radar.artisanspei.re, JSON-LD schema.org/Event generation, robots.txt rules, sitemap.xml generation, Strategie_SEO.md (external doc, iCloud), Google Search Console verification file, index.html — generated public site page (+7 more)

### Community 6 - "publier.py — publish pipeline"
Cohesion: 0.25
Nodes (13): apply_auto(), apply_pending(), find_latest_pending(), git_publish(), _host(), main(), official_domains(), Path (+5 more)

### Community 7 - "Public site UI (event cards)"
Cohesion: 0.19
Nodes (13): Candidature ouverte Badge (open-application status indicator), Ecrire Button (draft candidature action), Event Card: Fete des Agrumes (Le Tampon, Sud), Event Card: Franco Dan Sin Zil - Marche de createurs (Francofolies, Saint-Gilles-les-Bains, Ouest), Event Card: Rencontres de l'Artisanat (Plaine des Palmistes, Est), Plus d'infos Button (external link), Radar Marches - Agenda Evenements Screenshot, Search Bar (evenement/commune/organisateur/specialite) (+5 more)

### Community 8 - "README — architecture & incidents"
Cohesion: 0.18
Nodes (13): README — Radar Marchés (Agenda des Exposants), Auth /admin par mot de passe (remplace Replit-auth buggée), Assistant IA « Le ti artisan futé », Deux pièges launchd (iCloud sandbox + PATH minimal), Règle d'or : design figé (template.html intouchable), run_veille.sh (lance l'agent Claude headless), Liens tagués utm_source par canal, Piège corrigé : widget doit vivre dans template.html, pas index.html (+5 more)

### Community 9 - "Stats v2 brief — event lifecycle"
Cohesion: 0.21
Nodes (12): _CONTACT_TYPES filter (5 contact click types), daily_snapshot aggregate table, event_meta lifecycle tracking, PostgreSQL stats storage conventions, traffic_daily_legacy table (sentinel 1970-01-01), wa_subscribers (manual entry), Brief Statistiques v2 — capture exhaustive, Entonnoir de conversion (visite→fiche→contact→inscription) (+4 more)

### Community 10 - "capté ≠ publié — veille/ingest principle"
Cohesion: 0.21
Nodes (12): Principe « déposé ≠ publié », data/organizer_submissions.json (append-only), Playbook d'ingestion de documents (ingest_agent.md), doc_to_text.py extraction step, pipeline-requirements.txt (requests, python-dotenv, beautifulsoup4), __EVENTS__/__ORGS__/__LASTUPDATE__ placeholders, Playbook de veille (veille_agent.md) — agent « cerveau », Règle absolue « capté ≠ publié » (+4 more)

### Community 11 - "server.py: publish/retire/edit event"
Cohesion: 0.18
Nodes (12): _git_available(), _git_pull_for_publish(), _periodic_git_warning(), Pull doux (rebase) depuis GitHub avant d'écrire les données.      Retourne (ok:, Écrit events.json, met à jour meta.json, rebuild index.html depuis le     templa, Retire un événement publié (par nom exact) : events.json → rebuild →     push. R, Remplace la fiche d'un événement publié (repéré par son nom d'origine)     par l, Retourne True si un dépôt git est présent dans le répertoire courant. (+4 more)

### Community 12 - "build.py — template injection + JSON-LD"
Cohesion: 0.27
Nodes (10): build(), _js_literal(), _jsonld_events(), _load_json(), Path, Génère le bloc JSON-LD schema.org/Event (invisible, pour Google).      Règle str, Sérialise en littéral JS sûr à injecter dans <script>.      JSON est un sous-ens, Extrait une date SEULEMENT si jour + mois + année sont tous explicites.      Vol (+2 more)

### Community 13 - "Event metadata & publish dates"
Cohesion: 0.22
Nodes (10): _backfill_event_meta(), _parse_deadline_date(), _publish_event_to_repo(), _publish_event_to_repo_unlocked(), Extrait une date explicite (« 15/08/2026 » ou « 31 août [2026] ») du texte     l, Enregistre les métadonnées d'un événement. La date de publication n'est     jama, Complète event_meta pour les événements sans date de publication connue.     Sou, Publication sérialisée : voir _publish_event_to_repo_unlocked. (+2 more)

### Community 14 - "Admin decisions & completions cache"
Cohesion: 0.22
Nodes (10): _load_completions(), _load_decisions(), _norm_evname(), _published_name_zones(), Charge {key: {decision, ts}} depuis le fichier de décisions persisté., Charge {key: {status, event?, report?, ts}} (résultats de complétion IA)., Normalise un nom d'événement pour comparaison (dédoublonnage)., Couples (nom normalisé, zone) des événements déjà dans events.json.      events. (+2 more)

### Community 15 - "Admin dashboard sections"
Cohesion: 0.20
Nodes (10): _load_questions_stats(), _load_themes(), _load_traffic_stats(), _norm_email(), Trafic depuis PostgreSQL (vues live + historique repris des fichiers)., Section /admin : soumissions organisateurs en attente de relecture., Section /admin : événements actuellement publiés (Retirer / Corriger)., _render_org_submissions_section() (+2 more)

### Community 16 - "Traffic tracking (referrer/visitor hash)"
Cohesion: 0.25
Nodes (8): _categorize_referrer(), _categorize_source(), Enregistre une visite du site public (en base, via la file d'écriture)., Classe l'URL de référence en une source simple., Source de trafic : utm_source (prioritaire) puis referrer en repli., Identifiant visiteur anonymisé : hachage salé à sens unique, jamais l'IP., _record_visit(), _visitor_hash()

### Community 17 - "Claude model auto-check"
Cohesion: 0.25
Nodes (8): _check_models_once(), _fetch_model_ids(), _model_check_loop(), _model_tier(), Classe un modèle dans son tier d'après son nom, ou None si inconnu., Retourne la liste ordonnée des IDs de modèles actifs depuis l'API Anthropic., Vérifie que les modèles actifs sont toujours disponibles ; bascule si nécessaire, Thread daemon : vérifie les modèles au démarrage puis toutes les 24 h.

### Community 18 - "Email/webhook alerting"
Cohesion: 0.25
Nodes (8): Envoie un email via SMTP (mécanisme unique de l'app).     Retourne True en cas d, Alerte de démarrage en mode dégradé (compat historique)., Envoie une notification unique lors d'un démarrage en mode dégradé.     Tente le, Envoie une alerte via webhook (Slack / Discord / URL générique).     Retourne Tr, _send_degraded_alert(), _send_email(), _send_email_alert(), _send_webhook_alert()

### Community 19 - "GitHub↔Replit deploy pipeline"
Cohesion: 0.33
Nodes (7): GitHub push via x-access-token URL auth, Agent Memory Index, Rapport de suivi Radar Marchés 23/07/2026, publier.py --auto : publication Niveau 1 sécurisée, GitHub → Replit webhook deploy pipeline, publier.py (valide + build + push + redeploy), POST /sync GitHub webhook (HMAC-verified git pull)

### Community 20 - "doc_to_text.py — document extraction"
Cohesion: 0.48
Nodes (6): from_pdf(), from_zip_xml(), main(), Path, docx/odt : concatène le texte des XML internes., PDF texte via pdftotext (poppler) si dispo ; None si scan/absent.

### Community 21 - "Organiser CRM & admin briefs"
Cohesion: 0.33
Nodes (6): Brief formulaire self-service Organisateurs, data/organizer_crm.json (private CRM, no login), orgs.json dedup/enrichment by email, Cycle de vie événement (rapport organisateur commercial), Tableau de bord privé /admin, Threat Model (static single-file site, pre-backend snapshot)

### Community 22 - "run_veille.sh / run_weekly.sh scripts"
Cohesion: 0.40
Nodes (3): PATH, run_veille.sh script, run_weekly.sh script

### Community 23 - "Radar Marchés logo (image)"
Cohesion: 0.67
Nodes (4): Compass/radar sweep motif, Hand with spiral palm symbol, Logo Radar Marchés, Radar Marchés (project)

### Community 24 - "Replit auth redirect bug (screenshot)"
Cohesion: 0.67
Nodes (4): replit.com/auth_with_replit_redirect?domain=radar.artisanspei.re, radar.artisanspei.re (custom domain), Replit Workspace (François's Workspace), Replit Auth Redirect - Page Not Found Error

### Community 25 - "Admin password auth (screenshot)"
Cohesion: 0.67
Nodes (4): Admin password-protected access (not Replit-auth), Admin Dashboard 'Agenda Artisans Réunion', Git push error: replit-git-askpass missing / GitHub credentials unreadable, Dashboard Admin Screenshot with Git Push Error

### Community 26 - "git push error (screenshot)"
Cohesion: 0.67
Nodes (4): Dashboard Agenda Artisans Réunion (admin), Erreur git push - Invalid username or token, github.com/FHSERVICES974/radar-marches-reunion.git, Dashboard admin avec erreur git push

### Community 27 - "Per-event stats section"
Cohesion: 0.50
Nodes (4): _load_event_stats(), Stats par événement : vues de fiche, visiteurs uniques, clics contact., Section admin : statistiques par événement (30 derniers jours)., _render_event_stats_section()

### Community 28 - "Organiser submission → event conversion"
Cohesion: 0.50
Nodes (4): _month_from_text(), Devine le mois (1-12) depuis un texte de date en français, sinon 99., Construit la fiche 16 champs (même structure que les candidats IA)., _submission_to_event()

### Community 29 - "Admin stats + git error (screenshot)"
Cohesion: 1.00
Nodes (3): Admin Stats Dashboard (Visits/Uniques/Traffic Sources), Git Push Error: replit-git-askpass Not Found, Dashboard Agenda Artisans Réunion - Git Push Error Screenshot

### Community 30 - "Admin stats dashboard (screenshot)"
Cohesion: 0.67
Nodes (3): Admin Dashboard Screenshot (Agenda Artisans Réunion), Admin stats dashboard feature (visits/uniques 7d/30d, traffic chart, traffic sources), Git push error: replit-git-askpass missing / no GitHub credentials

### Community 32 - "Mobile capture (obsolete iPhone shortcut)"
Cohesion: 1.00
Nodes (3): Raccourci iPhone « Ajouter à Radar » (obsolete variant), data/inbox_mobile.txt (old file-based capture), data/inbox_mobile_export.txt (Apple Notes export via AppleScript)

## Ambiguous Edges - Review These
- `Tableau de bord privé /admin` → `Threat Model (static single-file site, pre-backend snapshot)`  [AMBIGUOUS]
  threat_model.md · relation: conceptually_related_to
- `Admin Dashboard 'Agenda Artisans Réunion'` → `Git push error: replit-git-askpass missing / GitHub credentials unreadable`  [AMBIGUOUS]
  attached_assets/image_1784951298210.png · relation: shares_data_with

## Knowledge Gaps
- **29 isolated node(s):** `ingest_docs.sh script`, `PATH`, `repl-nix-workspace`, `PATH`, `post-merge.sh script` (+24 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **4 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **What is the exact relationship between `Tableau de bord privé /admin` and `Threat Model (static single-file site, pre-backend snapshot)`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._
- **What is the exact relationship between `Admin Dashboard 'Agenda Artisans Réunion'` and `Git push error: replit-git-askpass missing / GitHub credentials unreadable`?**
  _Edge tagged AMBIGUOUS (relation: shares_data_with) - confidence is low._
- **Why does `esc()` connect `common.py — shared helpers` to `Admin decisions & completions cache`, `Admin dashboard sections`?**
  _High betweenness centrality (0.176) - this node is a cross-community bridge._
- **Why does `_render_proposals_section()` connect `Admin decisions & completions cache` to `Admin server: auth & submissions`, `common.py — shared helpers`, `server.py core (rate-limit, email, git)`, `Admin dashboard sections`?**
  _High betweenness centrality (0.069) - this node is a cross-community bridge._
- **Why does `_render_org_submissions_section()` connect `Admin dashboard sections` to `Admin server: auth & submissions`, `common.py — shared helpers`, `server.py core (rate-limit, email, git)`?**
  _High betweenness centrality (0.064) - this node is a cross-community bridge._
- **What connects `ingest_docs.sh script`, `PATH`, `repl-nix-workspace` to the rest of the system?**
  _29 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Admin server: auth & submissions` be split into smaller, more focused modules?**
  _Cohesion score 0.056338028169014086 - nodes in this community are weakly interconnected._