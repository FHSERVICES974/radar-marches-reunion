# Inventaire des procédures de candidature — appels à exposants (La Réunion)

> **Phase 0 du brief « Assistant Candidature ».** Ce document décrit les procédures **réellement
> observées**, sources ouvertes et lues. Il ne contient aucune extrapolation : quand une information
> n'a pas pu être vérifiée, elle est notée « non précisé » ou listée en angle mort (§7).
>
> Établi le 2026-08-01 · Corpus : `data/events.json` (100 événements)

---

## 0. Périmètre réellement couvert — à lire avant d'exploiter ce document

Le brief demandait de parcourir les 100 événements. Après mesure du coût réel (75 URL à ouvrir,
échecs fréquents), le périmètre a été **volontairement resserré** aux appels actionnables, en accord
avec François et avec le principe directeur du brief : *fiabilité sur un périmètre étroit plutôt que
couverture large et approximative*.

| Ensemble | Nombre | Traité ici |
|---|---|---|
| Total `events.json` | 100 | — |
| Marchés permanents (`perm`) | 30 | ❌ hors périmètre — pas de dossier de candidature, demande d'emplacement au fil de l'eau |
| Appels actionnables (ouverts / à venir avec deadline) | 29 | ✅ cible |
| **Sources effectivement ouvertes et lues** | **5** | ✅ dont 2 formulaires officiels analysés en profondeur |

⚠️ **Ce document est donc un socle, pas un inventaire exhaustif.** Les conclusions d'architecture
(§6) sont solides car elles reposent sur l'analyse fine de formulaires officiels réels ; les
statistiques de fréquence (§2, §3) sont indicatives et devront être consolidées en élargissant le
corpus.

### Sources ouvertes et lues

| Événement | Source | Résultat |
|---|---|---|
| La Plaine en Fête (Le Tampon) | page mairie + PDF « Bon de participation » | ✅ procédure complète |
| Franco Dan Sin Zil (Saint-Paul) | page avis de publicité + PDF fiche d'inscription | ✅ procédure complète |
| Braderie commerciale (Sainte-Marie) | page mairie | ⚠️ partiel — renvoie à un avis de publicité dont l'URL n'est pas publiée |
| Rencontres de l'Artisanat (Plaine des Palmistes) | site mairie | ❌ appel introuvable sur le site |
| Marché de Nuit St-Gilles, Saint-Paul Plage, Jour de sport santé | PDF repérés sur la page Saint-Paul | 🔗 URL collectées, non encore analysées |

---

## 1. Tableau des procédures observées

| Événement | Organisateur | Canal | Support de réponse | Pièces exigées | Champs demandés | Particularités |
|---|---|---|---|---|---|---|
| **La Plaine en Fête** | Ville du Tampon — Service Animation/Événements | Email **ou** dépôt physique | PDF « Bon de participation », **non remplissable**, à imprimer et signer | K-bis < 3 mois · attestation d'assurance · justificatif d'adresse · pièce d'identité recto/verso · attestation MSA (agriculteurs secteur Tampon) · **preuve de paiement des redevances précédentes** | Identité, date/lieu de naissance, société, SIRET, code APE, activité, nb de stands ou dimensions, raccordement électrique + puissance, équipements, **critères qualitatifs** (nature des produits, expérience pro, démonstrations, origine, savoir-faire O/N, qualité esthétique du stand + photos, déchets générés) | Sélection **sur critères qualitatifs**, pas premier arrivé · attestation sur l'honneur (législation du travail + assurances) · dépôt physique contre récépissé · consentement contact SMS/mail demandé |
| **Franco Dan Sin Zil** | Ville de Saint-Paul — Service Évènementiels Économiques et Marchés | Non précisé sur la page ; fiche à retourner | PDF fiche d'inscription, **scanné, non remplissable**, signature manuscrite | Pièce d'identité en cours de validité · justificatif d'adresse < 3 mois · **au choix** : registre du commerce OU récépissé auto-entrepreneur OU récépissé chambre des métiers · attestation formation hygiène alimentaire (si alimentaire transformé) · attestation assurance RC **professionnelle** · carte de marchand ambulant ou attestation · plusieurs photos de produits | Nom, prénom(s), adresse, code postal, ville, téléphone, e-mail, **détail des produits** | ⚠️ **« Les pièces jointes sont à transmettre exclusivement en PDF »** · « aucune copie ne sera faite sur place » · dossier complet exigé · pièce justificative **alternative** (3 options possibles) |
| **Braderie commerciale de Sainte-Marie** | Ville de Sainte-Marie — Service économique | Email : `serviceeconomique@ville-saintemarie.re` | Formulaire de candidature annoncé dans un « avis de publicité » **dont l'URL n'est pas publiée** | Non précisé | Non précisé | Profils ciblés : vêtement, accessoires, bijoux fantaisie, produits artisanaux, décoration · le document de référence n'est pas accessible en ligne |

---

## 2. Typologie des canaux

Sur les 100 événements, classification automatique à partir des champs `apply`, `contact` et `url`
(un événement peut cumuler plusieurs canaux) :

| Canal | Nombre | Part |
|---|---|---|
| Email | 68 | 68 % |
| Téléphone | 29 | 29 % |
| **Indéterminé** (donnée trop pauvre) | **22** | **22 %** |
| Réseau social (DM Instagram/Facebook) | 13 | 13 % |
| Formulaire à télécharger | 8 | 8 % |
| Courrier postal | 4 | 4 % |
| Dépôt physique | 3 | 3 % |
| Google Form | 1 | 1 % |

> **Lecture prudente.** Ces chiffres proviennent de nos propres résumés, pas des sources officielles.
> Les deux appels analysés en profondeur ont révélé un canal **mixte** (email *ou* dépôt physique) et
> des exigences absentes du résumé — la réalité est plus riche que ce tableau. Le chiffre le plus
> important est le **22 % d'indéterminés** : un cinquième du corpus n'a pas de procédure documentée.

**Conséquence pour l'ordre de développement :** l'email domine largement et reste le premier canal à
couvrir. Mais il ne suffit **jamais seul** — dans les deux cas analysés, l'email sert à transmettre
un **formulaire imprimé et signé** accompagné de pièces. L'outil « email » sans l'outil « dossier
assemblé » ne couvre aucun appel en entier.

---

## 3. Catalogue des pièces justificatives

Relevé sur les procédures officielles réellement lues (2 formulaires) :

| Pièce | Observée dans | Contrainte de validité | Remarque |
|---|---|---|---|
| Pièce d'identité | 2/2 | « en cours de validité » · recto/verso | Donnée sensible — chiffrement obligatoire |
| Justificatif d'adresse | 2/2 | **< 3 mois** | Absent du brief initial — à ajouter à la fiche artisan |
| Attestation d'assurance RC professionnelle | 2/2 | en cours de validité | Précision « professionnelle » explicite à Saint-Paul |
| K-bis / avis SIRENE | 1/2 | **< 3 mois** | Alternative acceptée à Saint-Paul (voir ci-dessous) |
| Récépissé auto-entrepreneur | alternative | — | **Au choix** avec K-bis ou chambre des métiers |
| Récépissé chambre des métiers | alternative | — | idem |
| Carte de marchand ambulant *ou attestation* | 1/2 | — | L'attestation est acceptée en substitut |
| Attestation MSA | 1/2 | — | Conditionnelle : agriculteurs, secteur géographique précis |
| Attestation formation hygiène alimentaire | 1/2 | — | Conditionnelle : vente d'alimentaire transformé |
| Photos de produits | 2/2 | — | « plusieurs » à Saint-Paul |
| Photos du stand | 1/2 | — | Sert à un **critère de sélection** (qualité esthétique) |
| Preuve de paiement des redevances précédentes | 1/2 | — | **Inattendu** — suppose un historique avec l'organisateur |

**Trois enseignements majeurs pour le « coffre à documents » :**

1. **Le justificatif d'adresse de moins de 3 mois** est aussi fréquent que le K-bis et n'était pas
   prévu au brief. Il périme, donc il doit être suivi.
2. **Les pièces alternatives existent** : le coffre ne peut pas être une simple liste de cases à
   cocher. Un artisan sans K-bis mais avec un récépissé de chambre des métiers est en règle. Le
   modèle de données doit gérer des **groupes d'équivalence**.
3. **Les pièces conditionnelles** dépendent de l'activité (MSA → agriculteur, hygiène → alimentaire)
   et parfois du **secteur géographique**. La fiche artisan doit porter ces attributs pour que
   l'Agent 2 sache quelles pièces sont réellement exigibles.

---

## 4. Catalogue des champs demandés

Champs relevés dans les deux formulaires officiels, normalisés :

| Champ | Fréquence | Type | Commentaire |
|---|---|---|---|
| Nom, prénom | 2/2 | texte | |
| Adresse complète (+ CP, ville) | 2/2 | texte | |
| Téléphone (fixe **et** portable) | 2/2 | texte | Le Tampon distingue les deux |
| Email | 2/2 | email | |
| Détail / nature des produits | 2/2 | texte long | Base des textes de candidature |
| SIRET | 1/2 | texte normalisé | |
| Code APE | 1/2 | texte normalisé | |
| Nom de société / qualité | 1/2 | texte | |
| Date et lieu de naissance | 1/2 | date + texte | Peu courant, mais bloquant si absent |
| Nombre de stands **ou** dimensions | 1/2 | nombre / texte | Correspond au « métrage linéaire » du brief |
| Raccordement électrique (O/N) + **puissance** | 1/2 | booléen + nombre | La puissance n'était pas prévue au brief |
| Équipements apportés | 1/2 | texte | |
| Consentement à être contacté (SMS / mail / non) | 1/2 | choix | RGPD — à conserver dans la fiche |

### Champs « critères qualitatifs » — la découverte la plus structurante

Le formulaire du Tampon ne demande pas seulement des informations administratives : il note le
candidat sur des **critères de sélection**.

- Nature et qualité des produits proposés
- Expérience professionnelle
- Démonstrations et animations proposées
- Origine des produits
- Produit valorisant un savoir-faire (O/N)
- Qualité esthétique et visuelle du stand (O/N) — **photos du stand exigées**
- Déchets générés par l'activité (O/N)

Plus une **attestation sur l'honneur** : être en règle vis-à-vis de la législation du travail et des
assurances.

> **Impact direct sur l'Agent 3.** Ces champs ne se remplissent pas par recopie de la fiche artisan :
> ce sont des textes argumentatifs à rédiger. C'est précisément là que l'assistance IA apporte le
> plus de valeur — et aussi là que le risque d'invention est le plus fort. La règle « aucune donnée
> inventée » doit s'appliquer **avec la même rigueur aux textes qualitatifs** : l'agent peut
> reformuler ce que l'artisan a déclaré, jamais inventer une expérience ou une démonstration.

---

## 5. Cas particuliers et pièges

| Piège | Observé sur | Conséquence si mal géré |
|---|---|---|
| **Formulaire PDF non remplissable** | 2/2 formulaires | L'artisan doit imprimer, remplir à la main, signer, scanner. Aucune automatisation de remplissage possible. |
| **Signature manuscrite obligatoire** | 2/2 | Aucun dossier ne peut être envoyé sans intervention physique de l'artisan. Impossible d'automatiser de bout en bout. |
| **Pièces exclusivement en PDF** | Franco Dan Sin Zil | Une photo JPEG d'un document fait rejeter le dossier. Conversion obligatoire. |
| **Sélection sur critères qualitatifs** | La Plaine en Fête | Ce n'est pas « premier arrivé, premier servi ». Un dossier complet mais fade peut être écarté. |
| **Pièces alternatives** | Franco Dan Sin Zil | Déclarer « K-bis manquant » à un artisan qui a un récépissé de chambre des métiers le ferait renoncer à tort — exactement le faux positif que le brief interdit. |
| **Pièces conditionnelles au secteur** | La Plaine en Fête (MSA « secteur Tampon ») | Exiger une pièce non applicable bloque un candidat éligible. |
| **Preuve de paiement antérieur** | La Plaine en Fête | Pièce qu'un nouvel exposant ne peut pas fournir. Le système doit gérer « non applicable ». |
| **Document de référence non publié** | Braderie Sainte-Marie | La page renvoie à un avis de publicité dont l'URL n'existe pas en ligne. Seul le contact direct permet de l'obtenir. |
| **Appel introuvable sur le site officiel** | Rencontres de l'Artisanat | L'URL enregistrée pointe vers l'accueil de la mairie, pas vers l'appel. |
| **Appel destiné à un organisateur, pas aux exposants** | Fête de la Vanille (relevé dans `apply`) | Un artisan qui candidate perd son temps. À détecter et signaler. |

---

## 6. Outils de production nécessaires — conclusions d'architecture

### 6.1 Ce qui est infirmé par le terrain

> ❌ **Le remplissage automatique de PDF à champs (AcroForm) est inutile.**
> Vérification technique sur les deux formulaires officiels : `AcroForm` absent, **0 champ
> `/Widget`**. Ce sont des documents à imprimer. La brique « remplissage AcroForm » prévue au brief
> ne couvrirait **aucun** appel réel connu à ce jour. À ne pas développer.

### 6.2 Ce que le terrain impose

| Brique | Couvre | Priorité | Justification |
|---|---|---|---|
| **Assemblage de dossier PDF** (pièces converties en PDF, ordonnées, fusionnées) | Les 2 appels analysés, probablement la majorité | 🔴 1 | Exigence explicite « pièces exclusivement en PDF ». Sans elle, aucun dossier n'est conforme. |
| **Conversion image → PDF** + compression | idem | 🔴 1 | Les artisans photographient leurs documents au téléphone. Certaines mairies limitent la taille des pièces jointes. |
| **Génération d'un formulaire pré-rempli imprimable** | 2/2 | 🔴 1 | Puisque le PDF officiel n'est pas remplissable : produire un document reprenant les champs avec les valeurs de l'artisan, à imprimer et signer. Fait gagner l'essentiel du temps. |
| **Rédaction d'email** + pièces jointes | 68 % (canal email) | 🟠 2 | Nécessaire mais **jamais suffisant seul** : l'email transporte le dossier assemblé. |
| **Rédaction assistée des critères qualitatifs** | 1/2 observé | 🟠 2 | Là où l'IA apporte le plus de valeur. Encadrer strictement contre l'invention. |
| **Lecture de PDF officiels** (texte **et** scanné) | tous | 🔴 1 | Prérequis de l'Agent 1. Voir 6.3. |
| Récapitulatif copiable pour formulaire web | 1 Google Form observé | 🟢 3 | Peu fréquent dans le corpus actuel. |

### 6.3 Contrainte technique confirmée sur la lecture des sources

Test réel effectué :

| Méthode | PDF texte (Le Tampon) | PDF scanné (Saint-Paul) |
|---|---|---|
| Récupération web directe | ❌ binaire illisible | ❌ binaire illisible |
| `doc_to_text.py` (outil du projet) | ✅ **extraction parfaite** | ❌ (scan, pas de couche texte) |
| Lecture visuelle du fichier | ✅ | ✅ **parfaite** |

> **Conclusion pour l'Agent 1 :** il doit **télécharger le PDF localement**, tenter l'extraction
> texte, et **basculer en lecture visuelle** en cas d'échec. L'outil `doc_to_text.py` existant couvre
> déjà le premier cas — il est réutilisable tel quel. Cette chaîne à deux niveaux est indispensable :
> une lecture directe par requête web échoue systématiquement sur les PDF.

---

## 7. Angles morts — ce qui n'a pas pu être vérifié

Listés explicitement plutôt que comblés par déduction, conformément à la règle de méthode du brief.

| Angle mort | Détail |
|---|---|
| **24 appels actionnables non ouverts** | Sur 29 sélectionnés, 5 sources seulement ont été lues. Les fréquences de §2/§3 sont donc indicatives. |
| **30 marchés permanents** | Hors périmètre par décision. Leur procédure (demande d'emplacement en mairie) reste à documenter si l'outil doit les couvrir. |
| **22 % d'appels au canal indéterminé** | Champ `apply` trop pauvre pour conclure ; nécessite l'ouverture de la source. |
| **Braderie Sainte-Marie** | L'avis de publicité contenant les modalités et le formulaire n'est pas publié en ligne. Obtenable uniquement par contact direct. |
| **Rencontres de l'Artisanat** | Appel introuvable sur le site de la mairie ; l'URL enregistrée pointe vers l'accueil. À re-sourcer. |
| **Marché de Noël Maison Banian** | Aucune URL enregistrée — procédure en deux temps (inscription puis paiement) connue seulement par notre résumé, non vérifiée. |
| **Appels via réseaux sociaux (13 %)** | Procédure derrière DM Instagram/Facebook, non consultable automatiquement. Restera dépendante d'une capture humaine. |
| **PDF collectés non analysés** | Marchés de Nuit Saint-Gilles, Saint-Paul Plage, Jour de sport santé — URL identifiées, contenu non lu. |

---

## 8. Recommandation pour la suite

1. **Élargir le corpus** avant de figer le cahier des charges : ouvrir les 24 sources restantes des
   appels actionnables, en priorité celles des 5 cas de test du brief. Les conclusions d'architecture
   de §6 sont déjà solides, mais les catalogues §3/§4 gagneront beaucoup en fiabilité.
2. **Ne pas développer** la brique « remplissage AcroForm ».
3. **Commencer par le lot 1** tel que le brief le définit — fiche artisan + Agent 1 + affichage des
   manques — en intégrant dès la conception les trois découvertes structurantes : groupes
   d'équivalence entre pièces, pièces conditionnelles (activité **et** secteur), et suivi de
   péremption du **justificatif d'adresse** au même titre que le K-bis.
4. **Prévoir dès le départ** que la chaîne ne peut pas être entièrement automatisée : la signature
   manuscrite impose une étape physique. L'objectif réaliste est de faire passer l'artisan d'une
   heure à cinq minutes **plus une impression-signature-scan**, pas de supprimer son intervention.
