# Playbook d'ingestion de documents — appels à candidature (artisans 974)

Tu traites les documents déposés dans `data/inbox_docs/` (appels reçus par mail,
scans, photos, formulaires). Chaque document est une **source de première main**
apportée par un humain : traite les événements qui en sortent comme **Vérifié**
(le document officiel fait foi), à condition d'y lire des **dates + lieu + modalités**.

## Règles
1. **N'écris JAMAIS `data/events.json`.** Tu écris une proposition + un pending
   (même format que la veille), pour validation humaine par `publier.py --apply`.
2. Ne traite QUE les fichiers présents dans `data/inbox_docs/` (hors `processed/`
   et `README.txt`). S'il n'y a aucun fichier, écris-le et arrête.
3. Ne devine rien : si une date/deadline manque dans le document, laisse le champ
   vide plutôt que d'inventer.

## Déroulé
1. Liste `data/inbox_docs/` (Bash `ls`). Pour chaque fichier (hors processed/, README) :
   - `.docx`, `.odt`, `.txt`, PDF **texte** → `python3 doc_to_text.py "<fichier>"`.
   - Si la sortie commence par `[SCAN]` (PDF scanné, image) → lis le fichier
     **visuellement avec l'outil Read** (pour un PDF, précise `pages`).
2. Extrais l'événement au **schéma EVENTS 16 champs** (voir `veille_agent.md`).
   Déduplique contre `data/events.json` (nom + zone normalisés) : si déjà présent,
   propose une MISE À JOUR au lieu d'un doublon.
3. Écris :
   - `proposition_docs_AAAA-MM-JJ.md` — résumé lisible (un bloc par document,
     avec le nom du fichier source et la confiance).
   - `data/pending/pending_docs_AAAA-MM-JJ.json` — format `publier.py --apply` :
     `new_events_candidates` avec le champ `event` **rempli** (objet complet) pour
     chaque appel bien daté, `_confidence: "Vérifié"`, `_source_url` = fichier ou URL.
4. Déplace chaque fichier traité vers `data/inbox_docs/processed/` (Bash `mv`).

## Fin
Termine par : `INGESTION TERMINÉE — proposition_docs_AAAA-MM-JJ.md`.
Ne lance jamais `publier.py`.
