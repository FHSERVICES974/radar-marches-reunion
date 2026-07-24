# Raccourci iPhone « Ajouter à Radar »

But : depuis un post Instagram/Facebook (ou n'importe quelle page), bouton
**Partager → Ajouter à Radar** → le lien + une note s'ajoutent à
`data/inbox_mobile.txt` dans le dossier du projet (synchronisé par iCloud).
La veille du lundi (ou `./ingest_docs.sh`) le reprend, en confiance « à confirmer ».

> Je ne peux pas installer le raccourci à distance sur votre iPhone ; voici la
> recette exacte à recréer dans l'app **Raccourcis** (2 minutes).

## Prérequis (une seule fois)
Le fichier cible doit exister. Sur le Mac, il est créé automatiquement au 1ᵉʳ dépôt,
mais pour être tranquille créez-le vide :
`data/inbox_mobile.txt` dans le dossier `radar-marches` (déjà dans iCloud Drive).

## Créer le raccourci
1. App **Raccourcis** → **+** (nouveau raccourci) → nommez-le **Ajouter à Radar**.
2. Icône ⓘ (réglages du raccourci) → activez **« Afficher dans la feuille de partage »**.
   - Type d'entrée accepté : **URL** et **Texte** (laissez les autres si besoin).
3. Ajoutez les actions, dans cet ordre :

   **Action 1 — Recevoir l'entrée**
   « Recevoir *URLs et Texte* depuis *Feuille de partage* ». (Souvent déjà présent.)

   **Action 2 — Demander une note (facultatif)**
   « Demander une saisie » → Type *Texte* → Invite : « Note : marché, commune,
   deadline ? ». Autorisez une réponse vide.

   **Action 3 — Lire le fichier existant**
   « Obtenir le contenu du fichier » → **Fichier** : choisissez
   `…/radar-marches/data/inbox_mobile.txt` (naviguez une fois, il s'en souvient).
   → désactivez « Erreur si introuvable » (ou « Afficher le sélecteur » = Non).

   **Action 4 — Construire la nouvelle ligne**
   « Texte » avec, sur des lignes/champs séparés par une tabulation :
   `[Date actuelle]  ⇥  [Entrée du raccourci]  ⇥  [Résultat de l'action Demander]`
   (Insérez les variables via le clavier de variables. « Date actuelle » = action
   *Date* ; format ISO recommandé aaaa-MM-jj.)

   **Action 5 — Ré-assembler fichier + nouvelle ligne**
   « Texte » = `[Contenu du fichier (action 3)]` puis saut de ligne puis
   `[Texte de l'action 4]`.

   **Action 6 — Réécrire le fichier**
   « Enregistrer le fichier » → **Destination** : le même
   `…/radar-marches/data/inbox_mobile.txt` → **Écraser** : Oui →
   « Demander où enregistrer » : Non.

4. Terminé. Testez : ouvrez un post Insta → **Partager** → **Ajouter à Radar** →
   tapez une note → le lien apparaît dans `inbox_mobile.txt`.

## Format d'une ligne produite
```
2026-07-24    https://instagram.com/p/xxxx    Marché de Noël St-Leu, deadline ?
```
La veille lit ce fichier, vérifie ce qu'elle peut, et met le reste en
« à confirmer » pour votre validation. **Jamais publié automatiquement** (c'est du social).

## Variante encore plus simple (sans manipuler le chemin de fichier)
Si l'action « Enregistrer/Écraser » vous ennuie : remplacez les actions 3-6 par
**« Ajouter à la note »** (Apple Notes) sur une note dédiée « Radar Inbox ».
On récupèrera ensuite ces liens côté Mac. Dites-le-moi si vous préférez cette voie,
je branche la lecture de la note.
