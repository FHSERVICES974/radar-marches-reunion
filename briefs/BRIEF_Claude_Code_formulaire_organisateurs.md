# Brief pour Claude Code — Formulaire self-service Organisateurs

## Contexte

Le site "Radar des Marchés" (`radar.artisanspei.re`, projet `radar-marches/`) fonctionne aujourd'hui dans un seul sens : François (+ un agent de veille) cherche les appels à candidature et les publie pour les artisans. Des organisateurs d'événements commencent à contacter François directement pour demander à être référencés (cas réel : une organisatrice de marché de créateurs à Saint-Paul).

Aujourd'hui, ce cas est traité **manuellement** : l'organisateur envoie les infos par message, François les relit et demande à un agent de les ressaisir dans `events.json`. Ça ne scale pas.

## Objectif

Créer une **page publique de dépôt d'événement**, où l'organisateur saisit lui-même ses informations et les soumet. Rien n'est publié automatiquement : la soumission atterrit dans la file de validation existante (même logique que `data/pending/` de la veille), François valide, corrige si besoin, puis publie avec `publier.py`.

**Principe directeur, identique à la veille : « déposé ≠ publié ».** Le formulaire ne fait que collecter proprement ; la validation humaine reste le seul chemin vers `events.json`.

## 🔒 Contrainte design

Cette page est **nouvelle**, elle n'est pas soumise à la règle de gel du design du dashboard public (`template.html`/`index.html` restent strictement intouchables, comme toujours). Mais elle doit être **visuellement cohérente** avec le site existant : mêmes polices (Fraunces/Inter), mêmes couleurs (émeraude `#0e6b52`, ivoire, dorée), même ton épuré. Elle doit avoir l'air de faire partie de la même marque, pas d'un outil tiers greffé dessus.

## Périmètre fonctionnel

### 1. Formulaire public — `/organisateurs` (ou `/deposer-un-evenement`)

Champs, alignés sur le schéma `EVENTS` existant pour éviter toute re-saisie ensuite :

| Champ formulaire | Correspond à | Obligatoire |
|---|---|---|
| Nom de l'événement | `name` | Oui |
| Zone (Nord/Est/Ouest/Sud/National) | `zone` | Oui (menu déroulant) |
| Type d'événement (menu + "Autre") | `type` | Oui |
| Nom de l'organisme/organisateur | `org` | Oui |
| Lieu précis | `place` | Oui |
| Date(s) / période | `when` | Oui |
| Date limite de candidature | `deadline` | Non (mais fortement encouragé) |
| **Lien(s) vers l'événement** (page officielle, post Instagram/Facebook) | alimente `url` + source pour vérification | Oui — au moins 1 lien. **C'est le champ clé : il remplace la re-saisie manuelle**, l'agent de veille peut aller lire la page pour vérifier/compléter. |
| Comment candidater (texte libre) | `apply` | Oui |
| Email de contact | `contact` | Oui |
| Téléphone (optionnel) | `contact` | Non |
| Réseau social (@compte) | `social` | Non |
| Description courte (2-3 phrases) | `desc` | Oui |
| **Pièce jointe** (affiche, PDF, flyer) — upload optionnel | stockée à part, liée à la soumission | Non — v1.1, voir plus bas |
| Nom et téléphone du déposant (pas forcément l'organisateur final) | traçabilité interne | Oui |

**Anti-spam minimal :** honeypot field (champ caché invisible humainement, rejette si rempli) + limite de fréquence par IP (ex. 5 soumissions / heure). Pas besoin de reCAPTCHA à ce stade — le volume attendu est faible.

### 2. Stockage des soumissions

Chaque soumission crée une entrée dans `data/organizer_submissions.json` (append-only), avec :
```json
{
  "id": "uuid",
  "submitted_at": "2026-08-01T10:32:00+04:00",
  "status": "à valider" | "validé" | "rejeté",
  "payload": { ...champs du formulaire... },
  "attachment_path": "data/organizer_uploads/<uuid>/affiche.pdf" | null,
  "reviewer_note": ""
}
```
Ne JAMAIS écrire directement dans `events.json` depuis le formulaire.

### 3. Pièces jointes (v1.1 — après la v1 texte/URL)

Comme discuté : commencer sans upload de fichier (le champ URL couvre déjà la majorité des cas — l'organisateur a presque toujours déjà une page ou un post). Ajouter l'upload seulement si les organisateurs le demandent en pratique.
Quand implémenté : stocker dans Replit Object Storage (vérifier l'offre actuelle à l'implémentation — l'API Replit évolue) ou un dossier serveur dédié, jamais dans le repo GitHub. Limiter taille (ex. 10 Mo) et types (PDF, JPG, PNG).

### 4. Notification à François

Dès qu'une soumission arrive : notification (email à shadowneox@gmail.com, ou notification macOS si détecté au prochain passage de `run_veille.sh`/`run_weekly.sh`). Ne pas attendre le lundi pour les soumissions organisateurs — elles doivent pouvoir être traitées au fil de l'eau, contrairement à la veille hebdo.

### 5. Interface de validation pour François

Étendre (ou créer, léger) une vue simple listant les soumissions `à valider`, avec pour chacune :
- Toutes les infos saisies + lien(s) cliquable(s) + pièce jointe si présente.
- Un bouton **Valider** : convertit la soumission au format `EVENTS`, l'ajoute à `data/events.json` (même chemin que `publier.py`), passe `status` à "validé", build + push + redeploy.
- Un bouton **Rejeter** (avec note optionnelle).
- Peut être une page simple protégée par le même mot de passe que `/admin`, ou une extension de `/admin` existant.

### 6. Confirmation à l'organisateur

Une fois validé, envoyer un email automatique simple à l'organisateur : "Votre événement est maintenant visible sur radar.artisanspei.re, merci !" — ça ferme la boucle et donne une image professionnelle.

### 7. Base de contacts organisateurs (CRM léger — PAS de compte/login)

**Choix explicite : pas d'authentification organisateur pour l'instant.** Ni mot de passe, ni lien magique — juste enrichir automatiquement une base de contacts à chaque soumission validée. Objectif : que François accumule sans effort une vraie base pour recontacter les organisateurs (partenariats, relances, futures offres), sans jamais leur demander de créer un compte.

**Fonctionnement :**
- À la validation d'une soumission (étape 5), **dédupliquer sur l'email** (normalisé, minuscules) contre `data/orgs.json` existant.
  - Si l'email correspond à un organisateur déjà connu → enrichir sa fiche existante (compléter les champs vides : téléphone, réseau social, site — ne jamais écraser une info déjà renseignée sans le signaler à François) et **incrémenter un compteur `events_soumis`** + mettre à jour `dernier_contact_le`.
  - Si nouvel email → créer une nouvelle entrée dans `data/orgs.json` (même structure que l'existant : `{n, m, t, s, w}`) + un fichier séparé **`data/organizer_crm.json`** pour les métadonnées privées non publiques : `{ email, date_premier_contact, date_dernier_contact, nombre_evenements_soumis, notes_internes: "" }`. **Ce fichier CRM ne doit jamais être exposé publiquement** (pas dans le répertoire affiché sur le site) — c'est un outil interne pour François, à garder hors du bundle public servi par `template.html`/`index.html`.
- Dans l'interface de validation (étape 5), afficher un badge "🆕 Nouvel organisateur" ou "🔁 Déjà connu (X événements soumis)" pour donner à François le contexte relationnel en un coup d'œil.
- Prévoir un export simple de `organizer_crm.json` en CSV (pour import dans un tableur ou un futur outil de mailing) — utile si François veut un jour faire une campagne de relance ou une offre premium aux organisateurs.

**Pourquoi pas de compte pour l'instant :** un compte avec mot de passe ajoute une vraie surface de risque et de support (récupération de mot de passe, sécurité des sessions) pour un bénéfice marginal tant que le volume d'organisateurs est faible. La déduplication automatique par email donne déjà l'essentiel : François connaît ses organisateurs, sait qui revient, et peut les recontacter — sans qu'aucun organisateur n'ait à gérer un identifiant de plus. Si le volume de soumissions récurrentes par les mêmes organisateurs devient important, un lien privé sans mot de passe (à la Calendly) reste une évolution possible plus tard, à évaluer alors seulement.

## Ce qui NE change PAS

- `template.html`, la logique d'affichage du dashboard public, le tri chronologique dynamique : intouchables.
- Le principe de validation humaine avant publication : intouchable, c'est ce qui protège la crédibilité du site.
- Le pipeline GitHub → Replit existant (webhook `/sync`) : le formulaire s'y insère, il ne le remplace pas.

## Priorité d'implémentation suggérée

1. Formulaire minimal (texte + URL, sans upload) + stockage `organizer_submissions.json` + notification.
2. Interface de validation simple (même mot de passe que `/admin`), avec badge nouvel/déjà-connu organisateur.
3. Déduplication par email + enrichissement de `data/orgs.json` et `data/organizer_crm.json` à la validation.
4. Confirmation email à l'organisateur validé.
5. Upload de pièces jointes (seulement si le besoin se confirme à l'usage).
6. (Plus tard, si besoin avéré) Lien privé sans mot de passe par organisateur récurrent.

## Résultat attendu

Un organisateur qui contacte François n'a plus qu'une réponse à donner : *"Voici le lien : radar.artisanspei.re/organisateurs"*. Zéro re-saisie manuelle côté François ou côté agent — seulement de la relecture et un clic de validation. En prime, François accumule automatiquement une base de contacts organisateurs qualifiée (qui, combien d'événements soumis, depuis quand), exploitable pour des relances ou des offres futures, sans qu'aucun organisateur n'ait à créer de compte.
