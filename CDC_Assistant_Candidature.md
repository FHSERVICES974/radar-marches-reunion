# Cahier des charges — Assistant Candidature · Lot 1

> Document destiné à l'agent Replit. Établi le 2026-08-02 · Version 1.0
> **À valider par François avant tout développement.**
>
> Ce lot couvre la **fiche artisan** et le **générateur de présentation d'activité**, plus la
> **plaquette PDF** qui en découle. Il est autonome, immédiatement utile, et sans risque juridique.

---

## 1. Contexte

**Radar des Marchés** (`radar.artisanspei.re`) recense les appels à candidature pour exposants à
La Réunion — marchés, foires, salons, environ 100 événements mis à jour chaque jour.

Ses utilisateurs sont des **artisans, créateurs et petits producteurs réunionnais** : bijoutiers,
travail du bois, textile, cuir, poterie, cosmétique, produits du terroir transformés. Ils exposent
sur des marchés et candidatent régulièrement auprès de mairies et d'organisateurs privés.

L'**Assistant Candidature** vise à réduire le temps qu'un artisan passe à candidater. Ce premier lot
en construit le socle : une fiche d'identité professionnelle que l'artisan remplit une fois, une aide
à la rédaction de sa présentation, et une plaquette PDF qu'il peut envoyer partout.

Le besoin est mesuré, pas supposé — sondage WhatsApp, **46 votes** (relevé du 11 août 2026) :

| Ce qui prend le plus de temps | Votes | Part |
|---|---|---|
| Remplir le formulaire ou le dossier | 20 | **43 %** |
| **Rédiger la présentation de son activité** | **12** | **26 %** |
| Rien, ça va vite | 9 | 20 % |
| Retrouver ses papiers à jour | 4 | 9 % |
| Comprendre ce qui est demandé | 1 | 2 % |

Ce lot traite les 26 %, et prépare les données qui serviront aux 43 % dans un lot ultérieur.

**Trois conséquences de conception :**

1. **70 % du besoin (32 votes sur 46) porte sur la production d'écrit**, pas sur l'administratif.
   L'outil doit générer du texte, pas ranger des fichiers.
2. **Le stockage de pièces justificatives n'intéresse que 4 personnes.** À ne pas confondre avec un
   besoin : c'est le plus petit segment du sondage après « comprendre ce qui est demandé ».
3. **Un seul répondant ne comprend pas ce qui est demandé.** Les fiches du radar remplissent déjà ce
   rôle — l'Assistant n'a pas à réexpliquer les appels, seulement à aider à y répondre.

---

## 2. Contraintes non négociables

Ces règles priment sur toute autre considération de ce document.

1. **Déploiement sous URL cachée, non indexée.** Aucun lien depuis `radar.artisanspei.re` ni depuis
   `www.artisanspei.re`. `robots.txt` en `Disallow` total, aucun sitemap, aucun lien entrant.
   L'accès se fait uniquement par lien direct transmis à la main.
   **Ne jamais mettre l'outil en production publique sans validation écrite de François.**
2. **Projet totalement séparé du Radar.** Ne toucher à aucun fichier du projet
   `radar-marches-reunion`, en particulier `template.html` et `build.py`.
3. **Aucune donnée inventée.** Le générateur reformule ce que l'artisan a déclaré. Il n'invente
   jamais une ancienneté, une récompense, un savoir-faire, une matière ou un lieu. Un champ inconnu
   reste vide et est signalé — jamais deviné.
4. **L'artisan valide tout.** Aucun texte généré n'est enregistré comme définitif sans relecture.
5. **Chiffrement au repos** de toute donnée sensible stockée.
6. **Consentement séparé et explicite** pour toute apparition publique de la fiche, décoché par
   défaut, révocable à tout moment.

---

## 3. Modèle de données

### 3.1 Table `artisan`

| Champ | Type | Obligatoire | Contraintes / valeurs |
|---|---|---|---|
| `id` | identifiant | oui | |
| `nom` | texte | oui | |
| `prenom` | texte | oui | |
| `nom_commercial` | texte | non | Nom de l'atelier ou de la marque |
| `email` | email | oui | Identifiant de contact |
| `telephone` | texte | non | |
| `siret` | texte | non | 14 chiffres si renseigné |
| `date_creation` | date | non | Création de l'entreprise |
| `forme_juridique` | choix | non | `micro_entreprise` · `entreprise_individuelle` · `sasu` · `sarl` · `association` · `autre` |
| `code_ape` | texte | non | |
| `adresse` | texte | non | |
| `commune` | choix | oui | Les 24 communes de La Réunion |
| `zone` | choix | oui | `Nord` · `Est` · `Ouest` · `Sud` |
| `metier` | choix | oui | Voir §3.2 |
| `metier_autre` | texte | conditionnel | Requis si `metier` = `autre` |
| `mots_cles` | liste de textes | non | 3 à 8 entrées |
| `description_courte` | texte | oui | 3 lignes · 150 à 300 caractères |
| `description_moyenne` | texte | non | ~10 lignes · 500 à 900 caractères |
| `description_longue` | texte | non | ~une demi-page · 1000 à 2000 caractères |
| `materiaux` | liste de textes | non | bois, cuir, vanille, coton… |
| `fait_main` | booléen | non | Plusieurs appels exigent « fait main prioritaire » |
| `production_locale` | booléen | non | |
| `gamme_prix` | texte | non | Ex. « 15 à 80 € » |
| `site_web` | URL | non | |
| `instagram` | texte | non | Compte sans le `@` |
| `facebook` | URL ou texte | non | |
| `whatsapp` | texte | non | |
| `logo` | image | non | PNG/JPG · max 5 Mo |
| `photos` | liste d'images | non | 3 à 10 · max 5 Mo chacune |
| `photo_stand` | image | non | Exigée par plusieurs mairies comme critère de sélection |
| `consentement_annuaire` | booléen | oui | **Décoché par défaut** · horodaté à chaque changement |
| `cree_le`, `modifie_le` | horodatage | oui | |

### 3.2 Liste normalisée des métiers

`bijoux` · `textile` · `bois` · `cuir` · `poterie_ceramique` · `cosmetique` ·
`alimentaire_transforme` · `produits_terroir` · `decoration` · `art_plastique` · `vannerie` ·
`bougies` · `papeterie` · `upcycling` · `autre`

Cette liste sert les candidatures **et** le futur annuaire. Ne pas l'étendre sans raison : un champ
libre non normalisé rend tout regroupement impossible ensuite.

### 3.3 Exemple de fiche complète

```json
{
  "id": "art_001",
  "nom": "HUBERT",
  "prenom": "François",
  "nom_commercial": "La Maison Opale",
  "email": "contact@lamaisonopale.re",
  "telephone": "0692 67 87 51",
  "siret": "90762879600017",
  "date_creation": "2021-11-15",
  "forme_juridique": "micro_entreprise",
  "code_ape": "3299Z",
  "adresse": "12 rue des Lataniers",
  "commune": "Saint-Denis",
  "zone": "Nord",
  "metier": "bougies",
  "metier_autre": null,
  "mots_cles": ["bougies artisanales", "cire végétale", "senteurs péi", "coulée à la main"],
  "description_courte": "La Maison Opale crée des bougies artisanales à la cire végétale, parfumées aux senteurs de La Réunion. Chaque pièce est coulée à la main dans notre atelier de Saint-Denis.",
  "description_moyenne": null,
  "description_longue": null,
  "materiaux": ["cire de soja", "mèche de coton", "verre recyclé"],
  "fait_main": true,
  "production_locale": true,
  "gamme_prix": "18 à 45 €",
  "site_web": "https://lamaisonopale.re",
  "instagram": "lamaisonopale.re",
  "facebook": null,
  "whatsapp": "0692678751",
  "logo": "logo_art_001.png",
  "photos": ["p1.jpg", "p2.jpg", "p3.jpg"],
  "photo_stand": "stand.jpg",
  "consentement_annuaire": false,
  "cree_le": "2026-08-02T10:14:00+04:00",
  "modifie_le": "2026-08-02T10:52:00+04:00"
}
```

---

## 4. Générateur de présentation d'activité

Un agent unique pour ce lot. Son rôle : **aider l'artisan à décrire son activité**, jamais écrire à
sa place à partir de rien.

### 4.1 Entrées

Ce que l'artisan a saisi : `metier`, `mots_cles`, `materiaux`, `fait_main`, `production_locale`,
`gamme_prix`, `nom_commercial`, `commune`, et un champ de notes libres (« Dites-nous en quelques mots
ce que vous faites et comment »).

### 4.2 Sortie attendue

Trois versions de la même présentation, produites en une seule fois :

```json
{
  "description_courte": "3 lignes, 150-300 caractères — sert aux candidatures et à la plaquette",
  "description_moyenne": "~10 lignes, 500-900 caractères — dossiers standards",
  "description_longue": "~une demi-page, 1000-2000 caractères — dossiers exigeants",
  "mots_cles_suggeres": ["…"],
  "champs_manquants": ["materiaux"],
  "message_utilisateur": null
}
```

- `champs_manquants` : les données absentes qui auraient permis un meilleur texte.
- `message_utilisateur` : renseigné **uniquement** si la génération n'a pas pu aboutir honnêtement.

### 4.3 Règles de rédaction — impératives

- **Ne jamais inventer.** Pas d'ancienneté (« depuis 15 ans »), de récompense, de label, de
  technique, de matière ou de lieu que l'artisan n'a pas fournis.
- **Ton** : simple, chaleureux, concret. Ni jargon marketing, ni superlatifs creux (« unique »,
  « exceptionnel », « passion dévorante »). Le lecteur type est un chargé d'animation de mairie qui
  lit quarante dossiers d'affilée.
- **Français de La Réunion** : accepter et respecter le vocabulaire local (« péi », noms de communes,
  matières locales) sans le corriger ni le franciser.
- **Proposer, jamais imposer.** Le texte généré s'affiche à côté de celui de l'artisan. Il choisit,
  et peut éditer librement le résultat.
- **Données insuffisantes → le dire.** Si l'artisan n'a fourni qu'un métier sans mots-clés ni notes,
  ne pas produire un texte creux : renseigner `message_utilisateur` avec une demande précise, par
  exemple « Ajoutez deux ou trois mots sur vos matières et votre façon de travailler, et je pourrai
  vous proposer une présentation. »

### 4.4 Comportement en cas d'échec technique

Si le service d'IA est indisponible, **la fiche reste entièrement utilisable en saisie manuelle**.
L'assistance est un confort, jamais un point de blocage. Afficher un message clair, pas une erreur
technique.

---

## 5. Plaquette PDF

Le livrable qui donne à la fiche une valeur autonome : un document que l'artisan peut envoyer à un
organisateur, joindre à une candidature, ou imprimer — même en dehors du Radar.

### 5.1 Contenu

- **En-tête** : logo de l'artisan (ou son nom commercial en typographie soignée si absent), nom
  commercial, métier, commune.
- **Présentation** : `description_moyenne` si elle existe, sinon `description_courte`.
- **Galerie** : 3 à 6 photos de produits, mise en page propre.
- **Repères** : matériaux, gamme de prix, fait main, production locale — **uniquement les champs
  renseignés**, sous forme de mentions courtes.
- **Contact** : email, téléphone, site, réseaux sociaux — uniquement les champs remplis.
- **Pied de page** : mention discrète « Fiche réalisée avec Artisans Péi ».

### 5.2 Exigences

- **Une page si possible**, deux au maximum.
- Format **A4**, imprimable proprement en couleur comme en noir et blanc.
- **Moins de 5 Mo** — plusieurs mairies limitent la taille des pièces jointes.
- La mise en page s'adapte aux champs réellement remplis : **aucune zone vide disgracieuse**.
- **Aucun texte factice ni image d'illustration générique.** Si l'artisan n'a pas de photo, la
  plaquette se compose sans galerie plutôt qu'avec des images de banque d'images.

---

## 6. Parcours utilisateur

| Écran | Contenu | Actions | Cas dégradés |
|---|---|---|---|
| **1. Accueil** | Trois lignes expliquant ce que fait l'outil | « Créer ma fiche » | — |
| **2. L'essentiel** | Nom, prénom, nom commercial, email, commune, zone, métier | Suivant | Champs obligatoires signalés à la saisie, pas après validation |
| **3. Mon activité** | Mots-clés, matériaux, fait main, production locale, gamme de prix, notes libres | Suivant · Passer | Tout est facultatif ici |
| **4. Ma présentation** | Zone de texte + bouton « M'aider à rédiger » | Générer · Choisir une version · Éditer · Valider | IA en échec : message clair, saisie manuelle possible. Données trop maigres : demande de précisions |
| **5. Mes visuels** | Logo, photos produits, photo de stand | Ajouter · Supprimer · Réordonner | Format ou poids refusé : message explicite indiquant quoi corriger |
| **6. Mes coordonnées** | Téléphone, site, Instagram, Facebook, WhatsApp | Suivant | Tout facultatif |
| **7. Ma fiche** | Aperçu complet + barre de complétude | Modifier · **Télécharger la plaquette PDF** | Complétude < 40 % : proposer de compléter avant export, **sans bloquer** |

### Principes de parcours

- **Saisie progressive.** Ne jamais exiger la fiche complète d'un coup. L'artisan doit pouvoir
  s'arrêter à n'importe quelle étape et retrouver sa fiche plus tard.
- **Barre de complétude** visible en permanence : « Votre fiche est complète à 65 % — il vous manque
  vos photos ». C'est le levier de motivation le plus efficace.
- **Mobile d'abord.** La majorité des artisans remplira depuis un téléphone, souvent via un lien
  WhatsApp. Zones tactiles confortables, ajout de photos depuis la pellicule.

---

## 7. Direction visuelle

Reprendre exactement la charte du Radar, pour que l'artisan reste dans le même univers.

```css
--bg:#f6f4ee;          /* fond ivoire */
--panel:#ffffff;       /* cartes */
--panel-alt:#faf9f4;   /* fond secondaire */
--ink:#211f1a;         /* texte principal */
--muted:#8a8474;       /* texte secondaire */
--muted-2:#b3ac98;
--line:#e7e1d2;        /* filets */
--accent:#0e6b52;      /* émeraude — actions principales */
--accent-soft:#e7f2ed;
--gold:#a9812f;        /* or — étiquettes, accents */
--gold-soft:#f6efe0;
--serif:'Fraunces',Georgia,serif;   /* titres */
--sans:'Inter',-apple-system,sans-serif; /* texte */
```

Registre : sobre, chaleureux, artisanal haut de gamme. Beaucoup de blanc, filets fins plutôt
qu'ombres lourdes. Ni startup tech, ni site administratif.

**À éviter** : photos de banque d'images, dégradés violet/bleu, animations de défilement lourdes.

---

## 8. Jeu de test

| Cas | Ce qu'il valide |
|---|---|
| **Fiche complète** — La Maison Opale (exemple §3.3) | Parcours nominal · trois versions générées · plaquette PDF complète |
| **Fiche minimale** — nom, email, commune, métier, description courte seulement | La plaquette reste présentable sans photos ni logo |
| **Aucune photo, aucun logo** | Mise en page dégradée propre, sans image factice |
| **Données trop maigres** — métier seul, aucun mot-clé, aucune note | Le générateur **demande des précisions** au lieu d'inventer. `message_utilisateur` renseigné |
| **Photo de 12 Mo** | Refus explicite avec message clair, ou compression automatique annoncée |
| **Service d'IA indisponible** | La fiche reste entièrement utilisable en saisie manuelle |
| **Texte en créole ou avec vocabulaire péi** | Respecté, non « corrigé » |
| **Artisan reprenant sa fiche 3 jours plus tard** | Données conservées, reprise à l'étape où il s'était arrêté |

**Critère de réussite du lot** : un artisan qui n'a jamais utilisé l'outil produit une plaquette PDF
présentable en **moins de dix minutes depuis son téléphone**, sans qu'aucune information fausse n'y
figure.

---

## 9. Hors périmètre de ce lot

À ne pas construire, même si cela paraît utile :

- **Analyse des appels à candidature** (Agent 1) et remplissage de dossiers — lot suivant.
- **Vérification des pièces justificatives** (Agent 2) — 4 votes sur 46, repoussé.
- **Coffre à documents et alertes de péremption** — dernier rang du besoin déclaré.
- **Annuaire public** des artisans — la fiche prépare les données, mais rien n'est publié tant que la
  base n'atteint pas une taille crédible et que le consentement n'est pas explicite.
- **Comptes utilisateurs avec mot de passe**, espace connecté, tableau de bord artisan.
- **Envoi de candidatures** au nom de l'artisan, mandat, mode délégué.
- **Facturation ou abonnement** — le service est gratuit en phase de test.

---

## 10. Conditions de livraison

Le lot est terminé quand :

1. Un artisan peut créer sa fiche de bout en bout depuis un téléphone.
2. Le générateur produit les **trois versions** de la présentation, honnêtes et éditables — ou
   explique clairement pourquoi il ne peut pas.
3. La plaquette PDF se télécharge, tient sur une à deux pages, pèse moins de 5 Mo, et reste
   présentable même sur une fiche minimale.
4. Les huit cas du jeu de test §8 passent.
5. L'outil est accessible **uniquement par lien direct**, non indexé, sans aucun lien entrant.
