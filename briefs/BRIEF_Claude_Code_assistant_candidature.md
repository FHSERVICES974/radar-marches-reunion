# Brief Claude Code — « Assistant Candidature » : agents IA d'aide au dépôt de dossier

> **Nouvelle brique produit.** Objectif : qu'un artisan puisse candidater à un appel en quelques
> minutes au lieu d'une heure, quel que soit le format demandé par l'organisateur (email + pièces,
> PDF à remplir, formulaire web, dépôt physique).
>
> **Statut :** phase de test, gratuit. Modèle cible à terme : abonnement mensuel (pas de facturation
> à l'acte). Aucune facturation n'est à implémenter dans ce chantier.

## 🔒 Mode de déploiement — accès caché, pas de mise en production

**Ce chantier ne se déploie PAS sur `radar.artisanspei.re` ni sur aucune URL publique/indexée.**

Développer la fonctionnalité sur Replit, sous une **URL non répertoriée** (ni liée depuis le Radar,
ni depuis la landing `artisanspei.re`, ni indexable — `robots.txt` en `Disallow`, pas de sitemap,
aucun lien entrant). L'accès se fait uniquement via le lien direct, transmis à la main par François
aux personnes qui testent.

**Pourquoi ce mode plutôt qu'un déploiement classique :**
- Le produit manipule des pièces d'identité et des données administratives — un test à ciel ouvert,
  même annoncé « bêta », expose à des utilisateurs non préparés et à des retours incontrôlés.
- François veut d'abord le tester lui-même en conditions réelles pour son propre atelier (**La
  Maison Opale**), avant de le faire essayer à un cercle restreint qu'il choisit personnellement.
- Rien ne doit apparaître comme un service public tant que le fonctionnement n'est pas validé sur
  plusieurs cycles de test.

**Séquence attendue :**
1. Développement sous URL cachée.
2. Test personnel de François avec les vraies données de La Maison Opale (SIRET, documents, appels
   réels) — c'est le premier jeu de test, avant tout tiers.
3. Une fois ce test concluant, François transmet le lien à quelques artisans choisis pour un test
   élargi.
4. Seulement après plusieurs cycles de test validés, décision **séparée et explicite** de François
   de passer en production (URL publique, liens depuis le Radar/la landing, annonce aux artisans).
   **Ne jamais franchir cette étape sans validation explicite** — ni Claude Code ni l'agent Replit
   ne doivent rendre l'outil public de leur propre initiative.

Cette règle prime sur toute mention contraire ailleurs dans ce document : tout ce qui, plus loin,
évoque un parcours artisan public, un lancement ou une annonce, est à comprendre comme la cible
*finale*, pas comme le périmètre de ce premier chantier.

---

## 0. Les deux règles qui cadrent tout le reste

### Règle 1 — « Préparé ≠ envoyé »
C'est le pendant exact du principe « capté ≠ publié » de la veille. L'agent **prépare** le dossier
complet. Ensuite, deux modes au choix de l'artisan :

| Mode | Comportement | Prérequis |
|---|---|---|
| **Mode A — Auto-envoi par l'artisan** (par défaut) | L'agent produit le dossier prêt : email rédigé, PDF rempli, pièces assemblées. L'artisan relit et envoie lui-même. | Aucun |
| **Mode B — Envoi délégué à Radar** | Radar envoie le dossier au nom de l'artisan. | **Mandat signé obligatoire** (voir §5) |

**Le mode A est toujours le défaut.** Le mode B ne s'active que si un mandat valide et non expiré
existe pour cet artisan.

### Règle 2 — Aucune invention de donnée
L'agent ne remplit **jamais** un champ dont il n'a pas la valeur dans la fiche artisan. Un champ
inconnu est signalé à l'artisan, pas deviné. Un dossier avec une donnée inventée (SIRET approximatif,
date erronée) peut faire rejeter la candidature et détruire la confiance — c'est le risque n°1 de ce
produit.

---

## 1. Architecture d'ensemble

Quatre agents en chaîne, pas un seul agent monolithique. Chacun a une responsabilité unique et un
format de sortie strict, ce qui rend le tout débogable et testable.

```
┌─────────────────────────────────────────────────────────────────┐
│ Agent 1 — ANALYSTE D'APPEL                                      │
│ Entrée : un événement de events.json + les URL de l'appel       │
│ Sortie : "fiche de procédure" structurée (JSON)                 │
│   → canal (email / PDF / formulaire web / physique)             │
│   → pièces exigées, champs demandés, deadline, destinataire     │
└──────────────────────────┬──────────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│ Agent 2 — VÉRIFICATEUR DE DOSSIER ARTISAN                       │
│ Entrée : fiche de procédure + fiche artisan + ses documents     │
│ Sortie : liste des manques (pièce absente, périmée, champ vide) │
│   → si manques bloquants : STOP, on demande à l'artisan         │
└──────────────────────────┬──────────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│ Agent 3 — RÉDACTEUR / REMPLISSEUR                               │
│ Entrée : fiche de procédure + fiche artisan complète            │
│ Sortie : dossier prêt (email rédigé, PDF rempli, champs mappés) │
└──────────────────────────┬──────────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│ Agent 4 — CONTRÔLEUR QUALITÉ                                    │
│ Relit le dossier produit avant présentation à l'artisan :       │
│ cohérence, champs vides, ton, pièces jointes bien présentes     │
│ → produit un score de complétude + liste d'alertes              │
└─────────────────────────────────────────────────────────────────┘
```

> **Pourquoi 4 agents et pas 1 :** un agent unique qui analyse + vérifie + rédige + contrôle produit
> des résultats instables et impossibles à diagnostiquer quand ça rate. Séparer permet de rejouer une
> seule étape, de logger chaque sortie, et d'améliorer un maillon sans casser les autres.

---

## 2. La fiche artisan (le socle de tout)

Sans fiche complète, aucun agent ne sert à rien. C'est le premier chantier à construire.

### 2.1 Contenu de la fiche

**Identité & entreprise**
- Nom, prénom · Nom commercial / atelier
- SIRET · Date de création · Forme juridique (micro-entreprise, association, EI…)
- Code APE · Adresse complète · Commune · Zone (Nord/Est/Ouest/Sud)
- Email · Téléphone · Site web · Réseaux sociaux

**Activité**
- Métier / catégorie (normalisée : bijoux, textile, bois, cuir, poterie, cosmétique, alimentaire transformé, produits du terroir…)
- Description courte de l'activité (2-3 phrases — sert de base aux textes de candidature)
- Description longue (10-15 lignes — pour les dossiers exigeants)
- Gamme de prix pratiquée
- Fait main ? Production locale ? Matériaux utilisés (beaucoup d'appels exigent « fait main prioritaire »)
- Photos de produits (3-10) et photo de stand

**Logistique d'exposition**
- Métrage linéaire habituel · Besoin électricité (O/N) · Matériel apporté (table, barnum, éclairage)
- Véhicule / capacité de transport
- Disponibilités (jours de la semaine, zones acceptées, distance max)

**Documents** (stockés, avec date d'expiration quand applicable)
- Pièce d'identité
- Extrait Kbis ou avis SIRENE (⚠️ souvent exigé **de moins de 3 mois** — d'où le suivi d'expiration)
- Attestation d'assurance responsabilité civile professionnelle (date d'expiration)
- Carte de commerçant ambulant (date d'expiration)
- Attestation MSA (pour les agriculteurs)
- Attestation de vigilance URSSAF
- RIB
- Book / photos produits
- Éventuel diplôme, label, certification (Artisan d'Art, Nou Lé Lokal…)

### 2.2 Alertes de péremption
Le système doit prévenir l'artisan **30 jours avant** l'expiration d'un document (assurance, carte
ambulant, Kbis > 3 mois). Un dossier rejeté pour un Kbis périmé est l'échec le plus fréquent et le
plus évitable.

### 2.3 Saisie progressive
Ne pas exiger la fiche complète d'un coup — c'est le meilleur moyen que personne ne la remplisse.
Demander le **minimum vital** au départ (identité, SIRET, métier, zone), puis compléter au fil des
candidatures : quand un appel exige une pièce absente, on la demande à ce moment-là et elle est
conservée pour les fois suivantes. **Le taux de complétude augmente à chaque dossier.**

Afficher une barre de complétude ("Votre profil est complet à 65 % — il vous manque l'attestation
d'assurance") : c'est le levier de motivation le plus efficace.

---

## 3. Agent 1 — Analyste d'appel

**Rôle :** transformer un appel à candidature (texte libre + PDF + page web) en procédure structurée.

**Entrées :** le champ `apply` et `url` de `events.json`, plus le contenu récupéré des URL officielles.

**Sortie attendue** (`data/procedures/<slug_evenement>.json`) :
```json
{
  "evenement": "La Plaine en Fête",
  "canal": "email",
  "destinataire": "animation.dac@mairie-tampon.fr",
  "deadline": "2026-07-31",
  "objet_suggere": "Candidature emplacement — La Plaine en Fête 2026",
  "pieces_exigees": [
    {"type": "kbis", "contrainte": "moins de 3 mois", "obligatoire": true},
    {"type": "assurance_rc_pro", "obligatoire": true},
    {"type": "piece_identite", "obligatoire": true},
    {"type": "attestation_msa", "obligatoire": false, "condition": "agriculteurs uniquement"}
  ],
  "champs_demandes": [
    {"nom": "categorie_emplacement", "type": "choix",
     "options": ["alimentaire", "non-alimentaire", "terroir transformé", "attraction enfants"]},
    {"nom": "metrage", "type": "nombre"}
  ],
  "tarifs": "3,50 à 50 €/jour selon catégorie",
  "confiance": "haute",
  "source_url": "https://letampon.fr/appels-candidatures/...",
  "date_analyse": "2026-07-29"
}
```

**Champ `confiance`** — indispensable :
- `haute` : formulaire/PDF officiel trouvé et analysé
- `moyenne` : procédure déduite du texte de l'appel, non confirmée par un document officiel
- `basse` : information partielle → **l'artisan doit être averti de vérifier auprès de l'organisateur**

**Ne jamais** présenter une procédure de confiance `basse` comme certaine.

### Cas particulier — dépôt physique
Certains appels exigent un dépôt en mairie. L'agent ne peut évidemment pas s'y rendre : il produit
alors un **dossier imprimable** (PDF unique, pièces assemblées dans l'ordre demandé) + l'adresse, les
horaires et un rappel de la deadline. C'est déjà 90 % du travail économisé.

---

## 4. Agents 2, 3, 4 — Vérification, production, contrôle

### 4.1 Agent 2 — Vérificateur
Compare la procédure aux données de l'artisan. Produit trois listes :
- **Bloquants** : pièce obligatoire manquante ou périmée → on ne va pas plus loin, on demande à l'artisan.
- **À compléter** : champ demandé absent de la fiche → question posée à l'artisan (et sauvegardée dans sa fiche pour la prochaine fois).
- **Avertissements** : l'artisan ne semble pas correspondre au profil recherché (ex. appel réservé aux producteurs agricoles). **Ne pas bloquer — informer.** C'est à l'artisan de décider.

### 4.2 Agent 3 — Rédacteur / remplisseur
Selon le canal :

- **Email** : rédige objet + corps du message à partir de la fiche artisan, avec les pièces en
  jointes. Ton professionnel, concis, adapté au destinataire (mairie ≠ association privée). Toujours
  personnalisé sur l'événement, jamais un template générique visible.
- **PDF à remplir** : remplissage des champs de formulaire (AcroForm) quand le PDF en contient. Si le
  PDF est un scan non remplissable → générer une **page de réponse annexe** reprenant les champs
  demandés, ou pré-remplir un gabarit à imprimer. Ne pas tenter d'écrire par-dessus une image scannée.
- **Formulaire web** : produire un **récapitulatif champ par champ** que l'artisan copie-colle, plutôt
  que d'automatiser le remplissage du navigateur en v1. Les formulaires web changent sans prévenir et
  l'automatisation navigateur est fragile — à réserver à une v2, sur les 2-3 formulaires réellement
  récurrents.
- **Dépôt physique** : PDF unique assemblé + fiche pratique (adresse, horaires, deadline).

### 4.3 Agent 4 — Contrôleur qualité
Relit systématiquement avant présentation :
- aucun champ laissé en `[à compléter]` ou avec un placeholder
- pièces annoncées = pièces réellement jointes
- cohérence des données (SIRET bien formaté, dates plausibles)
- deadline non dépassée
- ton et orthographe

Produit un **score de complétude** et une liste d'alertes affichée à l'artisan avant envoi.

---

## 5. Le mandat (mode B — envoi délégué)

Obligatoire avant tout envoi par Radar au nom de l'artisan. À traiter sérieusement : sans mandat,
Radar envoie un document administratif au nom d'un tiers sans autorisation.

**Contenu minimal du mandat :**
- Identité complète du mandant (l'artisan) et du mandataire (FHSERVICES / Radar des Marchés)
- Objet précis : transmission de dossiers de candidature à des appels à exposants
- **Périmètre** : soit global (tous appels), soit au cas par cas (recommandé au démarrage)
- Durée de validité (ex. 12 mois) et **révocation possible à tout moment**
- Mention explicite : Radar transmet le dossier, **ne garantit ni la recevabilité ni l'acceptation**
- Consentement au traitement des données personnelles et documents (RGPD)
- Date + signature

**Implémentation :** case à cocher + signature électronique simple (nom saisi + horodatage + IP
hachée) suffit pour ce niveau d'engagement. Conserver une trace horodatée de chaque acceptation.
Journaliser **chaque envoi effectué au nom d'un artisan** (date, destinataire, pièces, contenu) — c'est
la preuve en cas de contestation.

⚠️ Le mandat doit être **relu par un juriste ou un modèle validé** avant mise en production réelle.
Ne pas le rédiger uniquement par IA. C'est une action pour François, hors périmètre technique.

---

## 6. RGPD & sécurité (non négociable)

La fiche artisan contient des **données personnelles sensibles** : pièce d'identité, SIRET, RIB,
adresse. Le niveau d'exigence est bien supérieur à celui des statistiques anonymes du site.

- **Chiffrement au repos** des documents (pas de PDF d'identité en clair sur le disque).
- **Accès restreint** : seul l'artisan et François (admin) accèdent à une fiche. Journaliser les accès admin.
- **Droit à la suppression** : bouton « supprimer mon compte et tous mes documents », effectif sous 30 jours.
- **Durée de conservation** : documents supprimés après X mois d'inactivité (à définir, proposer 24 mois avec relance avant).
- **Politique de confidentialité dédiée** à cette fonctionnalité, distincte de la mention de mesure d'audience actuelle.
- **Ne jamais** envoyer une pièce d'identité à un destinataire non prévu par la procédure officielle.
- **RIB** : ne le collecter que si un appel l'exige réellement. Le plus souvent, non — dans le doute, ne pas le demander à l'inscription.

---

## 7. Parcours artisan (ce que voit l'utilisateur)

```
1. L'artisan crée son profil (minimum vital : 5 min)
2. Sur une fiche événement du radar → bouton « M'aider à candidater »
3. L'agent analyse l'appel et affiche :
   « Voici ce qui est demandé. Il vous manque : attestation d'assurance. »
4. L'artisan complète ce qui manque (conservé pour les fois suivantes)
5. L'agent produit le dossier → l'artisan le relit en entier
6. Choix : « J'envoie moi-même » (défaut) ou « Radar envoie pour moi » (si mandat)
7. Confirmation + rappel automatique si pas d'envoi 48h avant la deadline
```

**Point de conception essentiel :** l'artisan doit **toujours voir le dossier complet avant l'envoi**,
même en mode B. Une boîte noire qui envoie sans montrer est inacceptable pour un document
administratif engageant.

---

## 8. Ordre d'implémentation — fondé sur les besoins déclarés

### 📊 Sondage auprès des artisans (1ᵉʳ août 2026, 45 réponses)

> *« Quand vous candidatez à un appel, qu'est-ce qui vous prend le plus de temps ? »*

| Réponse | Votes | Part |
|---|---|---|
| **Remplir le formulaire ou le dossier** | 19 | **42 %** |
| **Rédiger la présentation de mon activité** | 12 | **27 %** |
| Rien, ça va vite pour moi | 9 | 20 % |
| Retrouver mes papiers à jour (Kbis, assurance…) | 4 | 9 % |
| Comprendre ce qui est demandé exactement | 1 | 2 % |

**Ce que ces chiffres imposent :**

- **69 % du besoin porte sur la production du dossier** (remplissage + rédaction). C'est là qu'il
  faut concentrer l'effort.
- **Le coffre à documents n'est PAS le besoin prioritaire** (9 %). Les artisans savent où sont leurs
  papiers. Cette brique, initialement prévue tôt, est repoussée en fin de parcours.
- **Comprendre l'appel n'est pas un problème** (2 %) : l'Agent 1 reste indispensable comme socle
  technique, mais ce n'est pas lui qui sera perçu comme la valeur par l'artisan.
- **20 % n'ont pas ce besoin.** Le service doit rester optionnel, jamais imposé dans le parcours du
  Radar.

⚠️ **Ne pas réordonner ce plan sur la base d'une intuition technique.** Il traduit ce que les
utilisateurs ont réellement déclaré. Toute modification de priorité doit être validée par François.

### Ordre retenu

| # | Chantier | Justification |
|---|---|---|
| 1 | **Fiche artisan** — version minimale (identité, SIRET, métier, zone, description d'activité) | Socle indispensable. Ne PAS commencer par la partie documents. |
| 2 | **Générateur de présentation d'activité** | 27 % du besoin. Écrit **une seule fois**, réutilisé à chaque candidature : meilleur rapport valeur/effort de tout le projet. |
| 3 | **Agent 1 — Analyste d'appel** sur `events.json` | Socle technique des étapes suivantes. Testable sans utilisateur. |
| 4 | **Agent 3 — remplissage : canal email** + assemblage des pièces | Cœur des 42 %. Premier dossier réellement livré. |
| 5 | **Agent 4 — Contrôleur qualité** | Obligatoire dès qu'on produit du contenu destiné à un tiers. |
| 6 | **Remplissage de PDF à champs** | Suite des 42 %. Volume à confirmer par `INVENTAIRE_PROCEDURES.md`. |
| 7 | **Agent 2 — Vérificateur de pièces** | Utile, mais seulement 9 % du besoin déclaré. |
| 8 | Canal **formulaire web** (récapitulatif copiable) + **dépôt physique** (dossier assemblé) | Compléter la couverture. |
| 9 | **Coffre à documents + alertes de péremption** | Confort. Rétrogradé : 9 % du besoin. |
| 10 | **Mandat + mode B** (envoi délégué) | Uniquement quand le mode A tourne de façon fiable. |

> **Étape 2 = premier jalon utile, et il arrive vite.**
> Un artisan saisit son métier, ses matériaux, sa gamme de prix, quelques mots sur sa démarche —
> l'outil produit trois versions de sa présentation : courte (3 lignes), moyenne (10 lignes), longue
> (une demi-page). Il les enregistre et les réutilise partout.
>
> Pas d'analyse d'appel, pas de pièces jointes, pas d'envoi : une seule fonction, immédiatement utile,
> sans aucun risque juridique. C'est ce qu'il faut livrer et faire tester en premier.

---

## 9. Tests d'acceptation

Utiliser de vrais appels déjà présents dans `events.json`, aux formats variés :

| Événement | Canal | Ce qu'il valide |
|---|---|---|
| **La Plaine en Fête** (Le Tampon) | Email + pièces | Kbis < 3 mois, catégories d'emplacement, attestation MSA conditionnelle |
| **Braderie de Sainte-Marie** | Email + formulaire mairie | Formulaire téléchargeable, deadline horaire précise (16h00) |
| **Franco Dan Sin Zil** (Saint-Paul) | Email ou dépôt physique | Double canal, critère « fait main prioritaire », tarif au mètre linéaire |
| **Marché de Noël Maison Banian** | Pré-inscription + paiement | Procédure en 2 temps (inscription puis paiement avant date) |
| **Rencontres de l'Artisanat** (Plaine des Palmistes) | Dossier complet | Dossier le plus exigeant, bon test de charge |

**Critère de réussite v1 :** sur ces 5 appels, l'agent identifie correctement le canal, le
destinataire, la deadline et au moins 90 % des pièces exigées — **sans jamais inventer une exigence
inexistante** (un faux positif est pire qu'un oubli : il fait renoncer un artisan éligible).

---

## 10. Hors périmètre de ce brief

- **Facturation / abonnement** : le service est gratuit en phase de test. Le modèle mensuel viendra
  dans un brief séparé, une fois l'usage réel mesuré.
- **Automatisation navigateur** des formulaires web (remplissage automatique) : v2, et seulement sur
  les formulaires récurrents.
- **Suivi de la réponse de l'organisateur** (accepté / refusé / sans réponse) : très intéressant à
  terme — c'est la donnée qui prouverait que Radar génère des candidatures **retenues** — mais à
  traiter séparément, après que le dépôt fonctionne.
- **Rédaction juridique du mandat** : action de François, avec un modèle validé.

---

## ⚠️ Le risque principal à garder en tête

Ce produit manipule des documents d'identité et envoie des dossiers administratifs au nom de tiers.
**Une seule erreur visible — un dossier envoyé avec le mauvais SIRET, une pièce d'identité fuitée —
coûterait bien plus que tout ce que la fonctionnalité peut rapporter.**

D'où les trois garde-fous non négociables : jamais de donnée inventée, l'artisan voit toujours le
dossier avant envoi, et rien n'est envoyé au nom de quelqu'un sans mandat signé.

Privilégier la **fiabilité sur un périmètre étroit** plutôt que la couverture large et approximative.

---

# 🔍 PHASE 0 — Cartographier les procédures réelles AVANT toute spécification

**Cette phase précède le cahier des charges.** On ne peut pas spécifier des outils de production de
documents sans savoir ce que les organisateurs réclament réellement. Toute architecture décidée avant
cet inventaire reposerait sur des suppositions.

## Ce qu'il faut faire

Parcourir **tous les appels à candidature de `data/events.json`** — en local, sans utilisateur, sans
interface — et pour chacun : lire le champ `apply`, ouvrir l'`url` officielle, récupérer les
formulaires et PDF liés quand ils existent, puis consigner la procédure réelle.

Les documents déjà déposés dans `data/inbox_docs/processed/` (affiches, captures, PDF d'appels)
font partie du corpus : ce sont des sources de première main, souvent plus détaillées que les pages
web.

## Ce qu'il faut produire

Un document `INVENTAIRE_PROCEDURES.md` à la racine, contenant :

**1. Un tableau exhaustif — une ligne par appel**

| Événement | Organisateur | Canal | Support de réponse | Pièces exigées | Champs demandés | Particularités |
|---|---|---|---|---|---|---|

Le champ « support de réponse » est le plus important : email libre, formulaire PDF remplissable,
PDF scanné non remplissable, formulaire web, Google Form, courrier papier signé, dépôt physique en
mairie, message privé Instagram…

**2. Une typologie consolidée des canaux**
Combien d'appels par type de canal, en valeur absolue et en pourcentage. C'est ce qui déterminera
l'ordre de développement : on construit d'abord l'outil qui couvre le plus de cas réels, pas celui
qui paraît le plus intéressant techniquement.

**3. Un catalogue des pièces justificatives**
Toutes les pièces rencontrées, leur fréquence d'apparition, et leurs contraintes (Kbis de moins de
3 mois, assurance en cours de validité, attestation MSA pour les agriculteurs, RIB, photos de
stand, book produits…). C'est ce catalogue qui définit la structure du « coffre à documents » et
les alertes de péremption.

**4. Un catalogue des champs demandés**
Tous les champs rencontrés dans les formulaires et dossiers, normalisés. Beaucoup se répètent d'un
organisateur à l'autre (nom, SIRET, activité, métrage, besoin électrique, catégorie d'emplacement) —
ce sont eux qui constituent la fiche artisan. Un champ qui revient dans plus de la moitié des appels
est un champ obligatoire de la fiche.

**5. Les cas particuliers et les pièges**
Procédures en deux temps (pré-inscription puis paiement), sélection sur dossier avec critères
qualitatifs, appels réservés aux membres d'une association, appels visant un organisateur unique et
non des exposants, deadlines à l'heure près, dossiers à retirer physiquement en mairie. Chaque cas
particulier mal anticipé produira un dossier rejeté.

**6. Les outils de production nécessaires**
Déduire de l'inventaire la liste des briques techniques à construire, avec pour chacune le nombre
d'appels qu'elle couvre :
- rédaction d'email + assemblage de pièces jointes
- remplissage de PDF à champs (AcroForm)
- traitement des PDF scannés non remplissables — que faire concrètement ?
- génération d'un courrier formaté, prêt à imprimer et signer
- récapitulatif copiable pour les formulaires web
- assemblage d'un dossier unique imprimable pour les dépôts physiques
- conversion et compression (certaines mairies limitent les pièces jointes à 5 Mo)

**7. Les angles morts**
Les appels dont la procédure n'a pas pu être déterminée : PDF inaccessible, page en erreur, réseau
social derrière authentification, information simplement absente. Les lister explicitement plutôt
que de combler par déduction.

## Règle de méthode

**Ne rien extrapoler.** Si un appel ne précise pas les pièces exigées, l'inventaire indique
« non précisé » — pas une liste probable. La valeur de ce document tient entièrement à sa fidélité
au réel ; une supposition écrite comme un fait se propagerait ensuite dans toute l'architecture.

## Pourquoi cet ordre

L'Agent 1 « Analyste d'appel » a pour rôle de **reconstruire automatiquement la procédure de réponse**
à partir d'un appel donné. Il ne peut le faire correctement que si l'on connaît d'abord l'éventail
complet des procédures existantes : ses catégories de sortie, ses champs, sa notion de « canal »
doivent venir du terrain, pas d'une liste imaginée à l'avance.

Cet inventaire est aussi un **actif durable** : il documente le fonctionnement réel du monde des
appels à exposants à La Réunion. Personne d'autre ne l'a fait.

---

# 📋 DEUXIÈME TÂCHE — rédiger le cahier des charges pour Replit

**À faire une fois `INVENTAIRE_PROCEDURES.md` produit et validé par François**, et pas avant : le
cahier des charges doit s'appuyer sur les canaux, pièces et champs réellement observés.

**Avant d'écrire la moindre ligne de code**, produire un document
`CDC_Assistant_Candidature.md` à la racine du projet. C'est ce document que François transmettra à
Replit ; le présent brief reste un document interne de cadrage et ne doit pas être donné tel quel.

## Pourquoi un document séparé

Ce brief explique **le pourquoi** — les arbitrages, les risques, le contexte du projet. Replit a
besoin **du quoi** : des spécifications exécutables, sans ambiguïté, sans avoir à comprendre
l'histoire du projet. Un agent qui doit interpréter des intentions produit des résultats
imprévisibles.

## Ce que le cahier des charges doit contenir

**1. Contexte minimal (1 page maximum)**
Ce qu'est Radar des Marchés, qui sont les artisans réunionnais, ce que fait l'Assistant Candidature.
Pas d'historique du projet, pas de considérations stratégiques ou commerciales.

**2. Contraintes non négociables, énoncées d'emblée**
- Déploiement sous URL cachée, non indexée, aucun lien entrant. **Jamais de mise en production
  sans validation écrite de François.**
- Projet **totalement séparé** de `radar.artisanspei.re` : ne toucher à aucun fichier du Radar, en
  particulier `template.html` et `build.py`.
- Aucune donnée inventée par un agent. Un champ inconnu est signalé, jamais deviné.
- L'artisan voit toujours le dossier complet avant tout envoi.
- Chiffrement au repos des documents d'identité.

**3. Modèle de données complet**
Schéma des tables ou collections, avec pour chaque champ : nom, type, obligatoire ou non,
contraintes, valeurs autorisées pour les champs normalisés (zone, métier, type de document).
Inclure les dates d'expiration des pièces. Fournir un exemple de fiche artisan complète en JSON.

**4. Spécification de chaque agent**
Pour les quatre agents : entrées exactes, sortie attendue **avec un exemple JSON complet**,
comportement en cas d'échec ou d'information manquante, critères de qualité. Le format de sortie de
l'Agent 1 (fiche de procédure) est le contrat central — il doit être décrit sans aucune ambiguïté.

**5. Parcours utilisateur écran par écran**
Chaque écran : ce qu'il affiche, les actions possibles, les états d'erreur, ce qui se passe ensuite.
Y compris les cas dégradés : profil incomplet, pièce expirée, deadline dépassée, agent en échec.

**6. Direction visuelle**
Reprendre la charte du Radar — variables CSS, polices Fraunces/Inter, palette ivoire/émeraude/or
(voir `template.html`). Mobile d'abord. Fournir les tokens directement dans le document, pour éviter
que Replit ait à aller les chercher.

**7. Jeu de test**
Les cinq appels réels de la section 9 de ce brief, avec pour chacun le résultat attendu de l'Agent 1.
C'est ce qui permet de vérifier objectivement que l'implémentation fonctionne. Compléter avec les cas
particuliers les plus délicats relevés dans `INVENTAIRE_PROCEDURES.md`.

**7 bis. Spécification des outils de production de documents**
Pour chaque brique identifiée dans l'inventaire (rédaction d'email, remplissage de PDF à champs,
génération de courrier, récapitulatif copiable, assemblage de dossier imprimable, compression) :
bibliothèque retenue, format d'entrée, format de sortie, comportement en cas d'échec. Indiquer le
nombre d'appels réels couverts par chaque brique — c'est ce qui justifie l'ordre de développement.

**8. Découpage en lots livrables**
Reprendre l'ordre d'implémentation de la section 8, en lots testables indépendamment. Préciser pour
chaque lot ce qui doit fonctionner pour qu'il soit considéré comme terminé. **Le lot 1 s'arrête à
l'étape 2** (fiche artisan minimale + générateur de présentation d'activité) — ne pas demander à
Replit de tout construire d'un coup. Ce lot est autonome, sans risque juridique, et répond déjà à
27 % du besoin déclaré.

**9. Ce qui est explicitement hors périmètre**
Facturation, comptes publics, automatisation navigateur, envoi délégué (mode B), suivi des réponses
d'organisateurs. L'énoncer clairement évite que Replit anticipe et construise des choses non
demandées.

## Exigences de rédaction

- **Français, ton directif.** « Le système doit… », pas « il serait souhaitable que… ».
- **Aucune question ouverte laissée dans le document.** Si un point n'est pas tranché, poser la
  question à François avant de finaliser plutôt que d'écrire « à définir ».
- **Des exemples concrets partout** : JSON, captures de structure, cas réels tirés d'`events.json`.
- **Autonome** : le lecteur ne doit avoir besoin d'aucun autre document.

Une fois `CDC_Assistant_Candidature.md` rédigé, le soumettre à François pour validation. Le
développement ne commence qu'après son accord.

Privilégier la **fiabilité sur un périmètre étroit** plutôt que la couverture large et approximative.
