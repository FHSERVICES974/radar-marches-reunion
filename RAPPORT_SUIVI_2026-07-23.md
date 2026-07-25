RAPPORT DE SUIVI — RADAR MARCHÉS
Agenda des Exposants — Artisans de La Réunion
23 juillet 2026


RÉSUMÉ EN UNE PHRASE
────────────────────
Le site a été refondu (design intact), une veille automatisée fiable a été
mise en place, et l'ensemble du pipeline GitHub → Replit fonctionne
désormais de bout en bout sans intervention manuelle.


① REFONTE DU SITE — DESIGN 100% PRÉSERVÉ
──────────────────────────────────────────
✅ Données séparées du design (data/events.json, data/orgs.json)
✅ Design isolé dans template.html — jamais modifié
✅ build.py régénère index.html à partir des données
✅ Non-régression prouvée : rendu identique à l'original, à l'octet près


② VEILLE AUTOMATISÉE — SOURCE-FIRST & VÉRIFIÉE
────────────────────────────────────────────────
✅ Une IA vérifie chaque source avant de proposer (pas un robot de mots-clés)
✅ Registre de sources à 4 niveaux de fiabilité :
      • 15 sources institutionnelles (CMA, Département, TCO, IRT…)
      • 24 mairies de l'île (avis de publicité, appels à forains)
      • 14 organismes privés d'événementiel
      • 10 lieux/hôtes (hôtels, domaines, parcs d'expo — dont Nordev)
      • 10 agrégateurs presse (pour découverte uniquement)
      • 20 comptes réseaux sociaux surveillés
✅ Principe strict : rien n'est publié sans validation humaine
      (sauf le Niveau 1 automatique, voir point ④)
🕓 Tourne automatiquement chaque LUNDI à 4H DU MATIN


③ CAPTURE TERRAIN — DOCUMENTS & MOBILE
─────────────────────────────────────────
✅ Documents (PDF, Word, photos de flyer) :
      déposez un fichier dans data/inbox_docs/ → l'IA en extrait
      l'événement, même sur un PDF scanné ou une photo
      → testé avec succès sur votre dossier "Rencontres de l'Artisanat"
        et un flyer scanné de la mairie de Saint-Paul

✅ Raccourci iPhone "Radar" :
      partagez un post Instagram/Facebook → capturé dans une note dédiée
      → un bug a été corrigé pour que la veille du lundi lise bien
        cette capture automatiquement
      → testé avec succès sur le Salon Régal (Nordev)


④ PUBLICATION AUTOMATIQUE — NIVEAU 1 (sécurisé)
──────────────────────────────────────────────────
✅ publier.py --auto publie automatiquement :
      • les changements de statut (dates dépassées, éditions passées)
      • les nouveaux événements "Vérifiés" venant de sources
        institutionnelles officielles uniquement
      • plafonné à 5 publications automatiques par semaine (sécurité)
⏸️  Tout le reste (réseaux sociaux, sources privées, incertain) reste
      en attente de votre validation manuelle
↩️  publier.py --rollback annule tout en une seule commande


⑤ ÉVÉNEMENTS DE LA BASE — 80 → 84
────────────────────────────────────
Nouveaux ajouts :
   • Salon Régal — Nordev, Saint-Denis — 23-25 octobre 2026
   • Rencontres de l'Artisanat — Plaine des Palmistes — 19-20 sept. 2026
   • Franco Dan Sin Zil — marché de créateurs, Saint-Gilles — 3 sept. 2026
   • Fête de l'ail — Petite-Île — 23-25 octobre 2026

Mises à jour :
   • Marché Zartizan Péi — dates confirmées 14-16 août 2026
   • Marché de Noël Maison Banian — deadline 20 août, tarif 210€,
     contact précisé


⑥ HÉBERGEMENT — GITHUB ↔ REPLIT, ENTIÈREMENT AUTOMATISÉ
────────────────────────────────────────────────────────────
✅ Dépôt GitHub : github.com/FHSERVICES974/radar-marches-reunion
✅ Connexion SSH configurée (push automatique, sans mot de passe)
✅ Domaine définitif en ligne : radar.artisanspei.re
      (certificat SSL valide jusqu'au 21/10/2026)
✅ Webhook GitHub → Replit activé ET testé avec succès :
      chaque mise à jour poussée se déploie automatiquement,
      sans aucun clic manuel


⑦ EN ATTENTE DE VOTRE DÉCISION
─────────────────────────────────
❓ Passer au cycle 100% automatique chaque lundi (veille + publication
    enchaînées), plutôt que la veille seule
❓ Marché de nuit de Saint-Benoît : existence confirmée, mais fréquence
    incertaine selon les sources (mensuel ou trimestriel) — pas ajouté,
    en attente d'une confirmation de votre part
ℹ️  Concert DMM (Saint-André) : détecté mais deadline déjà dépassée,
    non ajouté


REPÈRES — DOSSIER radar-marches/
───────────────────────────────────
README.md            → mode d'emploi complet
veille_agent.md       → instructions suivies par la veille
publier.py / build.py → scripts de publication
run_veille.sh         → lance la veille hebdomadaire
data/events.json      → les 84 événements (source de vérité)
data/sources.json     → le registre des sources surveillées


────────────────────────────────────────
Ce rapport reflète l'état du projet Radar Marchés au 23 juillet 2026.
