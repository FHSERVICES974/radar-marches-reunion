---
name: Stats PostgreSQL
description: Conventions du système de statistiques (tables, types d'interaction, snapshot, saisie manuelle)
---

- Les stats vivent en base (DATABASE_URL), jamais en fichiers locaux : le disque prod est réinitialisé à chaque Publish. Writer à backoff via `_stats_queue`, sentinelle `1970-01-01` dans `traffic_daily_legacy` (ne pas supprimer).
- **Pas de DDL au démarrage** : créer les nouvelles tables dans la base de dev (executeSql) ; le Publish provisionne le schéma en prod. **Why:** règle du projet, évite les migrations sauvages au boot.
- Clics contact : types `contact_email/phone/social/url` + ancien `candidater` conservé pour l'historique — toute requête « clics contact » doit filtrer sur `_CONTACT_TYPES` (les cinq).
- `daily_snapshot` : agrégat par jour, upsert horaire J-1..J-3 (rattrapage auto). `wa_subscribers` : **saisie manuelle** dans /admin, toujours affichée comme telle (badge ambre), jamais mélangée aux mesures auto.
- Sources : `utm_source` prioritaire (vérifié fonctionnel) ; referrer du site lui-même → `interne` ; WhatsApp sans utm → « direct » (l'appli n'envoie pas de referrer) — d'où l'importance des liens tagués sur radar.artisanspei.re.
- `event_meta` (published_on jamais écrasée, deadline_on parsée du texte libre — None si pas de date explicite) : rattrapage idempotent au fil de la boucle snapshot depuis les commits git « Publier : X » ; le rapport organisateur compare en fenêtres homogènes publication→min(limite, aujourd'hui) et omet la comparaison sous 3 pairs avec vues.
- Tout `event_name` venant de `/track` est du texte non fiable : `html.escape` obligatoire à chaque rendu dans /admin (XSS stockée déjà tentée en test).

## Nettoyage de données en prod
La base prod est en lecture seule pour l’agent (SELECT uniquement). Toute suppression/écriture prod doit passer par le serveur de production lui-même (ex. purge idempotente dans la boucle d’entretien horaire, effective ~2 min après un Publish).
- Questions chatbot : table `chat_questions` (ts, question, model_tier — jamais de hash/IP), écriture via `_stats_queue` (« cq »), rétention 24 mois, reprise legacy transactionnelle avec sentinelle `1970-01-02` dans traffic_daily_legacy (ne pas supprimer).
- Sentinelles dans traffic_daily_legacy : 1970-01-01 (import clicks), 1970-01-02 (import questions), 1970-01-03 (nettoyage visites non-page) — ne jamais les supprimer.
- Consultations de fiches : type `event_read` (intérêt réel, dédupliqué par visiteur) ; `event_view` = legacy gonflé, plus jamais affiché ni accepté par /track ; visites = page `/` + UA non-bot uniquement.
