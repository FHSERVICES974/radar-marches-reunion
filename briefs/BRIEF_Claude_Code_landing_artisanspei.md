# Brief Claude Code → Replit — Landing page « Artisans Péi »

> **Objet :** créer la page d'accueil de la marque mère `artisanspei.re`, aujourd'hui vide.
> Le Radar (`radar.artisanspei.re`) devient l'un des outils de cette plateforme, pas le tout.

---

## 🔒 Contrainte technique n°1 — à lire avant de toucher à quoi que ce soit

**La landing page doit être un déploiement Replit SÉPARÉ du Radar.**

Le Radar (`radar.artisanspei.re`) a un design figé, généré par `build.py` à partir de `template.html`.
Si l'agent Replit intervient sur le même projet, il peut régénérer ou écraser ce template — et tout
le travail du Radar serait perdu.

- Nouveau Repl, nouveau dépôt Git, nouveau domaine (`artisanspei.re` / `www.artisanspei.re`).
- **Aucun fichier du projet Radar n'est modifié par ce chantier.**
- Les deux sites communiquent uniquement par des liens.

---

## 1. Le concept — ce qu'est Artisans Péi

**Artisans Péi est une plateforme d'outils au service des artisans, créateurs et producteurs
réunionnais.**

Sa vocation tient en une phrase :

> **Libérer le temps des artisans de tout ce qui n'est pas la création.**

Un artisan péi passe des heures chaque semaine à chercher des opportunités éparpillées sur Facebook
et Instagram, à remplir des dossiers de candidature, à retrouver ses attestations, à comprendre quelles
démarches administratives s'appliquent à lui. **Ce temps-là n'est pas du temps de création.** C'est du
temps perdu, et c'est celui qu'Artisans Péi veut lui rendre.

### Le positionnement en une ligne (à utiliser comme accroche)

> **« Votre temps, c'est votre matière première. »**

Alternatives possibles si celle-ci ne convient pas :
- « Moins de paperasse. Plus de création. »
- « Les outils qui rendent du temps aux artisans péi. »

### Ce qu'Artisans Péi n'est PAS (important pour éviter les malentendus)

- Ce n'est **pas** une place de marché : on ne vend pas les créations des artisans.
- Ce n'est **pas** un réseau social de plus.

C'est une **boîte à outils** pour le quotidien professionnel de l'artisan.

### L'annuaire : une conséquence, pas une promesse de départ

À terme, les artisans qui s'enregistrent pour utiliser les outils constitueront **de fait un annuaire
des artisans péi** — c'est un actif majeur pour la suite (crédibilité auprès des organisateurs,
mise en relation, valeur pour d'éventuels partenaires).

**Mais l'annuaire ne se construit pas en le demandant, il se construit en rendant service.** Un
artisan s'inscrit parce qu'il veut ne plus rater d'appel à candidature, pas pour figurer dans une
liste. L'annuaire est le résultat de cette inscription, pas son argument.

**Conséquences concrètes pour la landing page :**
- **Ne pas l'annoncer sur la page à ce stade.** La promesse reste « des outils qui vous font gagner du
  temps ». Mettre l'annuaire en avant maintenant, avec peu d'inscrits, affaiblirait la proposition.
- **Mais concevoir la fiche artisan dès maintenant en prévision de cet usage** (voir
  `BRIEF_Claude_Code_assistant_candidature.md` §2) : métier normalisé, zone, description d'activité,
  photos. Ces champs servent déjà aux candidatures — et alimenteront l'annuaire le jour venu, sans
  avoir à redemander quoi que ce soit aux artisans.
- **Prévoir un consentement explicite et séparé** : « J'accepte que mon atelier apparaisse dans
  l'annuaire public des artisans péi » — décoché par défaut, modifiable à tout moment. Publier un
  artisan sans son accord serait une faute RGPD et une rupture de confiance.

> À revoir quand la base atteindra une taille crédible (≈ 100-150 artisans inscrits avec une fiche
> complète). En dessous, un annuaire clairsemé dessert plus qu'il ne sert.

### Une plateforme collaborative — tout le monde peut signaler un événement

C'est un point différenciant à afficher clairement sur la page : **n'importe qui peut transmettre un
appel à candidature, pas seulement les organisateurs.**

| Qui | Ce qu'il transmet | Pourquoi il le fait |
|---|---|---|
| **Un organisateur** | Son propre appel à exposants | Trouver des exposants, gratuitement |
| **Un artisan** | Un appel qu'il a repéré ailleurs (Facebook, Instagram, affiche, bouche-à-oreille) | Aider les autres — et faire vivre l'outil dont il se sert |

**Pourquoi c'est important :** un appel diffusé uniquement dans un groupe Facebook fermé ou sur une
affiche en mairie échappe à la veille automatique. Ce sont précisément ceux-là que les artisans
ratent. Les artisans sur le terrain sont le meilleur capteur possible — ils voient ce qu'aucun robot
ne voit.

**Le cadre reste identique : « transmis ≠ publié ».** Tout signalement, d'où qu'il vienne, passe par
la même file de relecture humaine avant publication. La qualité de la base est ce qui fait la valeur
du Radar ; l'ouvrir à tous ne veut pas dire la laisser se dégrader.

**Conséquences pour la landing page :**
- Une section propre, formulée du point de vue de l'artisan :
  > **Vous avez repéré un appel que d'autres pourraient rater ?**
  > Transmettez-le en deux minutes. On vérifie, on publie, et toute l'île en profite.
  > *(Organisateur ou artisan — tout le monde peut contribuer.)*
- Lien vers `https://radar.artisanspei.re/organisateurs`
- **Renommer la page de dépôt** : « Espace organisateurs » exclut implicitement les artisans. Préférer
  **« Proposer un événement »** ou **« Signaler un appel »**, avec un sous-titre du type *« Organisateur
  ou artisan, transmettez un événement à publier »*. Le formulaire lui-même n'a pas besoin de changer,
  seulement son intitulé et son introduction. ⚠️ Cette modification concerne le projet Radar, pas la
  landing page — à traiter séparément pour respecter la séparation des deux déploiements.
- Le bloc organisateurs du bas de page (§3.6) devient donc un **bloc contribution**, ouvert aux deux
  publics.

---

## 2. Le fil conducteur de la page — le cycle de l'artisan

Structurer la page autour du cycle réel de l'artisan qui expose. C'est ce qui rend l'offre lisible
et montre que les outils forment un ensemble cohérent, pas une collection de gadgets.

```
   TROUVER          →      CANDIDATER        →       PRÉPARER
   les opportunités        sans y passer             sans rien oublier
                           des heures

   ✅ Radar des Marchés    🔜 Assistant             🔜 Coffre à documents
      (en ligne)              Candidature              (à venir)
                              (à venir)
```

À chaque étape, un problème concret et l'outil qui y répond.

---

## 3. Structure de la page, section par section

### 3.1 En-tête (hero)

- **Sur-titre** : `LA RÉUNION · OUTILS POUR ARTISANS`
- **Titre** : *Artisans Péi*
- **Accroche** : « Votre temps, c'est votre matière première. »
- **Sous-titre** (2 lignes max) :
  > Une plateforme d'outils pensés pour les artisans, créateurs et producteurs péi. On s'occupe de la
  > recherche, de la paperasse et des rappels — vous gardez votre temps pour créer.
- **Bouton principal** : « Découvrir le Radar des Marchés » → `https://radar.artisanspei.re/`
- **Bouton secondaire** : « Rejoindre la communauté » → ancre vers le bloc d'inscription

> ⚠️ **Pas de chiffres inventés dans le hero.** Ni « X artisans nous font confiance », ni « X
> événements ». Si des chiffres sont affichés un jour, ils devront venir de données réelles. Une
> promesse fausse détectée par un artisan local détruit la confiance instantanément — et à La
> Réunion, tout le monde se connaît.

### 3.2 Le problème (section courte, empathique)

Trois constats, formulés du point de vue de l'artisan, sans dramatiser :

- **Les opportunités sont partout et nulle part.** Les appels à candidature circulent sur Facebook,
  Instagram, les sites de mairies, le bouche-à-oreille. On les rate sans même savoir qu'ils existaient.
- **Candidater prend du temps.** Chaque organisateur a son formulaire, ses pièces, ses délais. On
  recommence tout à chaque fois.
- **L'administratif s'accumule.** Kbis de moins de 3 mois, attestation d'assurance, carte de
  commerçant ambulant… on les cherche toujours au dernier moment.

### 3.3 Les outils

**Outil 1 — Radar des Marchés** · badge `EN LIGNE`
> Tous les marchés, foires, salons et appels à candidature de La Réunion, réunis en un seul endroit
> et mis à jour chaque jour. Filtrez par zone, repérez les dates limites, ne ratez plus une
> opportunité.
>
> Inclut **« Le ti artisan futé »**, un assistant qui répond à vos questions sur les démarches.
>
> → Bouton : « Accéder au Radar » (lien avec `?utm_source=landing&utm_medium=site&utm_campaign=outil_radar`)

**Outil 2 — Assistant Candidature** · badge `BIENTÔT`
> Vous choisissez un appel, on prépare votre dossier : le bon formulaire, les bonnes pièces, le
> message rédigé. Vous relisez, vous envoyez. Ce qui prenait une heure prend cinq minutes.
>
> → Bouton : « Être prévenu au lancement » (ouvre le bloc d'inscription, pré-coché sur cet outil)

**Outil 3 — Coffre à documents** · badge `BIENTÔT`
> Vos pièces administratives au même endroit, toujours à jour. On vous prévient avant qu'une
> attestation n'expire — plutôt que de le découvrir la veille d'une deadline.
>
> → Bouton : « Être prévenu au lancement »

> **Règle sur les badges :** `BIENTÔT` doit rester honnête. Pas de fausse date de sortie. Un outil qui
> reste « bientôt » pendant six mois est moins grave qu'un outil promis pour septembre et absent en
> décembre.

### 3.4 Ce qui nous guide (section confiance)

Quatre engagements courts, qui différencient réellement :

- **Vérifié avant publié.** Chaque appel est relu par un humain avant d'apparaître. Pas de robot qui
  recopie n'importe quoi.
- **Gratuit pour les artisans.** Le Radar l'est et le restera dans sa version de base.
- **Vos données vous appartiennent.** Aucun cookie publicitaire, aucun traceur tiers, aucune revente
  de données. Jamais.
- **Fait à La Réunion, pour les artisans d'ici.** Les zones, les communes, les organisateurs
  locaux — parce qu'on connaît le terrain.

### 3.5 Inscription (le cœur de la page)

Bloc simple, sans friction :
- Choix de la zone : Nord / Est / Ouest / Sud / Toute l'île
- Métier ou activité (champ libre, facultatif)
- Bouton **WhatsApp** (principal) + lien email discret en repli
- Cases à cocher : « Ce qui m'intéresse » → Radar des Marchés · Assistant Candidature · Coffre à documents

> **Pourquoi les cases à cocher :** elles disent lesquels des outils à venir intéressent réellement les
> artisans. C'est le meilleur signal de priorisation qu'on puisse obtenir — et il est gratuit.

Reprendre le mécanisme WhatsApp déjà en place sur le Radar (lien `wa.me` pré-rempli vers
`262692678751`), en adaptant le message pré-écrit.

### 3.6 Bloc contribution (en bas de page)

Un seul bloc, deux publics :

> **Un événement à faire connaître ?**
>
> **Vous organisez** un marché, une fête, un salon ? Déposez votre appel à exposants gratuitement.
>
> **Vous êtes artisan** et vous avez repéré un appel ailleurs ? Transmettez-le : on vérifie, on publie,
> et toute l'île en profite.
>
> Chaque proposition est relue avant publication.
> → `https://radar.artisanspei.re/organisateurs`

Volontairement en bas de page : la page parle aux artisans en priorité. Mais le bloc doit exister —
des organisateurs arriveront sur `artisanspei.re` en cherchant qui se cache derrière le Radar, et les
artisans doivent savoir qu'ils peuvent contribuer.

### 3.7 Pied de page

- Une ligne sur FHSERVICES (l'entité derrière la plateforme) — crédibilité pour organisateurs et
  partenaires qui vérifient à qui ils ont affaire.
- Contact : email + WhatsApp
- Liens : Radar · Espace organisateurs · Mentions légales · Confidentialité
- Mention : « Aucun cookie publicitaire, aucun traceur tiers. »

---

## 4. Direction artistique — cohérence avec le Radar

**Impératif :** un artisan qui passe de `artisanspei.re` à `radar.artisanspei.re` doit sentir qu'il
reste chez le même acteur. Reprendre exactement les tokens du Radar (`template.html`) :

```css
--bg:#f6f4ee;        /* ivoire */
--panel:#ffffff;
--panel-alt:#faf9f4;
--ink:#211f1a;
--muted:#8a8474;
--line:#e7e1d2;
--accent:#0e6b52;    /* vert émeraude */
--accent-soft:#e7f2ed;
--gold:#a9812f;      /* or */
--gold-soft:#f6efe0;
--serif:'Fraunces', Georgia, serif;   /* titres */
--sans:'Inter', system-ui, sans-serif; /* texte */
```

**Registre visuel :** sobre, chaleureux, artisanal haut de gamme. Beaucoup de blanc, titres en
Fraunces, bordures fines plutôt que des ombres lourdes. Ni startup tech, ni site institutionnel.

**À éviter absolument :**
- les photos d'illustration génériques de banques d'images (mains d'artisan anonymes, ateliers
  scandinaves) — elles sonnent faux ici. Préférer l'absence d'image à une image fausse. À terme,
  remplacer par de vraies photos d'artisans réunionnais, avec leur accord.
- les dégradés violets/bleus type SaaS
- les animations et effets de défilement lourds

**Mobile d'abord.** L'immense majorité des artisans consultera depuis un téléphone, souvent depuis un
lien WhatsApp. Le rendu mobile prime sur le rendu desktop.

---

## 5. Technique

- **Page statique** (HTML/CSS/JS en un seul fichier, comme le Radar). Pas de framework, pas de build
  complexe : la page changera rarement.
- **Performance** : chargement < 1,5 s en 4G. Pas de bibliothèque externe autre que les polices Google.
- **Accessibilité** : contrastes AA, navigation clavier, textes alternatifs, taille de police de base
  ≥ 16 px.
- **Formulaire d'inscription** : même destination que celui du Radar, pour éviter deux bases de
  contacts séparées. À aligner avec Claude Code sur l'endpoint existant.

### SEO
- `<title>` : `Artisans Péi — Les outils qui font gagner du temps aux artisans réunionnais`
- `meta description` : reprendre le sous-titre du hero
- `<link rel="canonical" href="https://artisanspei.re/">`
- Balises Open Graph (la page sera beaucoup partagée sur WhatsApp — l'aperçu doit être soigné :
  image, titre, description)
- `robots.txt` + `sitemap.xml` propres à ce domaine
- Redirection de `www.artisanspei.re` vers `artisanspei.re` (ou l'inverse), pas les deux actifs — même
  problème de contenu dupliqué que celui déjà traité sur le Radar.

### Mesure
- Même approche que le Radar : mesure interne anonyme, **aucun cookie publicitaire, aucun traceur
  tiers, pas de Google Analytics.**
- Tagger tous les liens sortants vers le Radar en UTM (`utm_source=landing`), pour savoir combien de
  visiteurs la landing envoie réellement vers l'outil.
- Suivre : visites, clics vers le Radar, inscriptions, et **quelles cases « ce qui m'intéresse » sont
  cochées** (le signal de priorisation produit).

---

## 6. Ce qui est hors périmètre

- Comptes utilisateurs, espace connecté, tableau de bord artisan → viendront avec l'Assistant
  Candidature, pas ici.
- Pages détaillées par outil → une seule page pour l'instant. On en créera si le besoin apparaît.
- Blog / articles → plus tard, quand il y aura de la matière.
- Version anglaise / créole → non prioritaire.
- Toute facturation ou abonnement → aucun outil n'est payant à ce stade.

---

## 7. Critère de réussite

La page est réussie si un artisan qui la découvre depuis un lien WhatsApp comprend en **moins de dix
secondes** :

1. à qui ça s'adresse (à lui),
2. ce que ça lui apporte (du temps),
3. ce qu'il peut faire tout de suite (aller sur le Radar, ou s'inscrire).

Si l'une des trois n'est pas immédiate, la page est trop chargée. **Dans le doute, retirer plutôt
qu'ajouter.**
