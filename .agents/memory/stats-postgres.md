---
name: Stats PostgreSQL
description: Conventions du système de statistiques (tables, types d'interaction, snapshot, saisie manuelle)
---

- Les stats vivent en base (DATABASE_URL), jamais en fichiers locaux : le disque prod est réinitialisé à chaque Publish. Writer à backoff via `_stats_queue`, sentinelle `1970-01-01` dans `traffic_daily_legacy` (ne pas supprimer).
- **Pas de DDL au démarrage** : créer les nouvelles tables dans la base de dev (executeSql) ; le Publish provisionne le schéma en prod. **Why:** règle du projet, évite les migrations sauvages au boot.
- Clics contact : types `contact_email/phone/social/url` + ancien `candidater` conservé pour l'historique — toute requête « clics contact » doit filtrer sur `_CONTACT_TYPES` (les cinq).
- `daily_snapshot` : agrégat par jour, upsert horaire J-1..J-3 (rattrapage auto). `wa_subscribers` : **saisie manuelle** dans /admin, toujours affichée comme telle (badge ambre), jamais mélangée aux mesures auto.
- Sources : `utm_source` prioritaire (vérifié fonctionnel) ; referrer du site lui-même → `interne` ; WhatsApp sans utm → « direct » (l'appli n'envoie pas de referrer) — d'où l'importance des liens tagués sur radar.artisanspei.re.
- Tout `event_name` venant de `/track` est du texte non fiable : `html.escape` obligatoire à chaque rendu dans /admin (XSS stockée déjà tentée en test).
