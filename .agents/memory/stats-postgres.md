---
name: Stats persistantes PostgreSQL
description: Où vivent les statistiques du site et les pièges du cycle de déploiement.
---
Les statistiques (page_views, interactions, traffic_daily_legacy) vivent dans la base PostgreSQL Replit (DATABASE_URL), plus jamais dans des fichiers locaux.
**Why:** le disque de la VM prod est réinitialisé à chaque Publish ; tout fichier local non committé (ex-traffic.json/clicks.jsonl) était effacé — c'était la cause des compteurs à zéro.
**How to apply:** toute nouvelle métrique va en base via la file `_stats_queue` (writer thread avec backoff, jamais de fichier). Lectures via `_stats_query`. Hachage visiteur salé par SESSION_SECRET. Rétention 24 mois automatique. utm_source prioritaire sur le referrer. La ligne sentinelle `1970-01-01` dans traffic_daily_legacy marque l'import legacy fait — ne pas la supprimer. Serveur passé en ThreadingHTTPServer.
