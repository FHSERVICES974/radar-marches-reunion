# Brief Claude Code — Statistiques v2 : capture exhaustive & valorisation

> **Principe directeur : on ne peut pas reconstruire rétroactivement une donnée qu'on n'a pas
> collectée.** Chaque jour sans capture est perdu définitivement. L'objectif de ce brief est de
> capturer MAINTENANT tout ce qui servira dans 3, 6 ou 12 mois — pour vendre aux organisateurs,
> convaincre des sponsors, et démontrer la crédibilité de la plateforme.

---

## 🔴 Priorité 0 — Vérifier que la persistance tient réellement

**Constat inquiétant sur le dashboard du 26/07/2026 :**
`167 visites (7 j)` = `167 visites (30 j)` = `167 total historique`, et le graphique n'affiche que
2 jours (25 et 26 juillet).

Deux explications possibles — **il faut trancher avant tout le reste** :
- (a) La migration vers PostgreSQL a eu lieu le 25/07 et l'historique repart légitimement de zéro → OK, rien à corriger.
- (b) Les données sont **toujours effacées** à chaque déploiement → problème critique non résolu.

**Actions :**
1. Vérifier en base (`SELECT min(occurred_at), max(occurred_at), count(*) FROM page_views;`) que les
   lignes antérieures au 25/07 existent ou non.
2. Test d'acceptation formel : noter le nombre de lignes, déclencher un déploiement complet,
   recompter. **Le compte doit être identique ou supérieur, jamais réinitialisé.**
3. Documenter le résultat dans le README (date de début réelle de l'historique fiable).

---

## 🟠 Priorité 1 — Corriger les trous de mesure actuels

### 1.1 « Fiches consultées » anormalement bas
3 fiches consultées pour 62 visiteurs uniques et 92 événements publiés. Ce n'est pas crédible :
soit l'événement de tracking ne se déclenche que dans un cas très étroit (clic sur un bouton précis),
soit il ne remonte pas. **Une "consultation de fiche" doit être déclenchée dès qu'un utilisateur
consulte réellement le détail d'un événement** (ouverture/déploiement de la fiche, ou passage
significatif dans le viewport — ex. > 2 secondes visible).

### 1.2 « Clics Écrire » à 0
Vérifier que l'écouteur est bien branché sur TOUS les liens de contact (mailto, tel, réseaux sociaux,
« Plus d'infos »). Distinguer les types : `contact_email`, `contact_phone`, `contact_social`, `contact_url`.

### 1.3 Sources : 63 « Autre » sur 167
Les liens UTM ne sont manifestement pas utilisés partout (1 seule visite attribuée à WhatsApp alors
que c'est le canal principal). Deux actions :
- Vérifier que la lecture d'`utm_source` fonctionne réellement (test avec une URL taguée).
- Améliorer la détection par referrer en repli (domaines Facebook, Instagram, Google, Messenger, Bing, LinkedIn).
- Documenter dans le README **la liste des liens tagués à utiliser** (mise à jour avec le domaine
  `radar.artisanspei.re`, pas `radar.fhservices.re`).

### 1.4 Écart inscriptions
11 inscriptions WhatsApp trackées, alors que le groupe compte ~40 membres. Normal si le tracking est
récent, mais **ajouter un champ de saisie manuelle** dans `/admin` pour enregistrer chaque semaine le
nombre réel d'abonnés WhatsApp (le site ne peut pas le connaître automatiquement). Cette série
temporelle est un actif majeur pour les sponsors et organisateurs.

---

## 🟢 Priorité 2 — Nouvelles données à capturer dès maintenant

### 2.1 Snapshot quotidien (assurance-vie des données)
Créer une table `daily_snapshots`, alimentée automatiquement chaque nuit :
```
date, visites, visiteurs_uniques, nouveaux_visiteurs, visiteurs_recurrents,
evenements_publies, evenements_ouverts, fiches_consultees, clics_contact,
abonnes_whatsapp (saisie manuelle), questions_chatbot, soumissions_organisateurs
```
**Pourquoi c'est critique :** même si une table de détail est purgée ou corrompue un jour, la série
historique agrégée survit. C'est la donnée qu'on ne peut jamais reconstituer.

### 2.2 Visiteurs récurrents vs nouveaux
Distinguer, via le `visitor_hash`, les nouveaux visiteurs des visiteurs qui reviennent.
**Argument de vente sponsor/organisateur** : un visiteur qui revient vaut bien plus qu'un visiteur de
passage — c'est la preuve d'une audience fidèle, pas d'un pic accidentel.

### 2.3 Cycle de vie d'un événement (pour les rapports organisateurs)
Pour chaque événement, enregistrer :
- `date_publication` sur le radar,
- `date_deadline`,
- courbe des vues **jour par jour** entre les deux,
- total vues / uniques / clics contact,
- **comparaison à la moyenne** des événements de la même zone/type.

**C'est LE livrable commercial** : *« Votre appel a été vu 84 fois par 61 artisans en 12 jours, soit
2,3× la moyenne des événements de l'Ouest. »* Un organisateur paie pour ça.

### 2.4 Recherches internes sur le site
Logger (anonymement) les termes tapés dans la barre de recherche du dashboard.
**Double valeur :** signal de demande (quels métiers/zones cherchent les artisans → quoi prioriser
dans la veille) + mots-clés réels pour le SEO.

### 2.5 Profil d'audience agrégé (pour sponsors)
- Répartition **mobile / desktop**.
- Répartition **horaire et par jour de la semaine** (utile aussi pour conseiller les organisateurs :
  « publiez le mardi matin, c'est le pic d'audience »).
- Zone géographique déclarée des abonnés (depuis le formulaire d'inscription : Nord/Est/Ouest/Sud).
- Métiers déclarés des abonnés (champ libre → normaliser en catégories).

### 2.6 Entonnoir de conversion
Mesurer et afficher le parcours complet :
```
Visite → Consultation de fiche → Clic contact → Inscription
```
avec taux de passage à chaque étape. C'est la métrique qui prouve que le site **génère des
candidatures**, pas seulement du trafic. Argument central pour l'offre payante organisateurs.

### 2.7 Croissance (séries temporelles)
Courbes mois par mois : visiteurs, abonnés, événements publiés, organisateurs actifs.
**Un sponsor n'achète pas une photo, il achète une trajectoire.** Sans historique de croissance,
aucun dossier de sponsoring ne tient.

---

## 🔵 Priorité 3 — Améliorations du dashboard `/admin`

1. **Comparaison période N vs N-1** (« +38 % vs 7 jours précédents ») sur chaque KPI.
2. **Filtre de période** (7 / 30 / 90 jours / tout l'historique).
3. **Distinguer visuellement** les KPI « données réelles » des KPI « saisie manuelle » (abonnés WhatsApp).
4. **Section « Kit média »** : une vue synthétique exportable en PDF regroupant les chiffres
   présentables à un partenaire/sponsor (audience, croissance, engagement, portée cumulée
   site + WhatsApp + réseaux). À générer à la demande.
5. **Export CSV global** de `daily_snapshots` (pour analyse externe, tableur, dossier de subvention).
6. **Alerte visuelle** si aucune donnée n'a été enregistrée depuis > 24 h (détecter une panne de
   tracking sans attendre de s'en rendre compte des semaines plus tard).

---

## ⚖️ Garde-fous (à ne pas franchir)

- **Aucune donnée personnelle identifiable.** Le hachage salé des IP reste la règle. Pas de cookie
  publicitaire, pas de traceur tiers, pas d'empreinte de navigateur (fingerprinting).
- Les métiers/zones des abonnés proviennent **uniquement de ce qu'ils déclarent volontairement**.
- Rétention 24 mois, purge automatique (déjà en place — conserver).
- Les données agrégées communiquées à un organisateur ou un sponsor ne doivent **jamais** permettre
  d'identifier un visiteur individuel.
- Mettre à jour la mention en pied de page si de nouvelles catégories de mesure sont ajoutées.

---

## Ordre d'implémentation recommandé

| # | Chantier | Pourquoi ce rang |
|---|---|---|
| 1 | Vérifier la persistance (Priorité 0) | Tout le reste est inutile si les données s'effacent |
| 2 | Snapshot quotidien (2.1) | Assurance immédiate contre toute perte future |
| 3 | Corriger fiches consultées + clics contact (1.1, 1.2) | Métriques centrales du rapport organisateur |
| 4 | Cycle de vie événement (2.3) | Le livrable qui se vendra en premier |
| 5 | Sources / UTM (1.3) | Savoir quel canal marche vraiment |
| 6 | Récurrents, entonnoir, recherches (2.2, 2.4, 2.6) | Profondeur d'analyse |
| 7 | Kit média + exports (3.4, 3.5) | Quand il y aura des chiffres à montrer |

> ⚠️ **Ne pas sur-construire.** L'audience actuelle est de ~60 visiteurs uniques. L'objectif ici
> n'est pas de bâtir un outil d'analyse de niveau entreprise, mais de **s'assurer qu'aucune donnée
> utile n'est perdue** pendant la phase de démarrage. Privilégier la capture simple et fiable à la
> visualisation sophistiquée.
