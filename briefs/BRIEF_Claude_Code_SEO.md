# Brief pour Claude Code — Fondations SEO & indexation Google

## Contexte

Le site public (`radar.artisanspei.re`) doit être mieux indexé par Google. Une analyse a identifié des problèmes techniques concrets à corriger en priorité, avant toute stratégie de contenu. Voir `Strategie_SEO.md` (dans le dossier ARTISANS iCloud de François, ou demander le contenu si besoin) pour le raisonnement complet — ce brief se concentre sur l'implémentation.

## 🔒 Rappel de la contrainte design

Tout ce qui suit est **invisible visuellement** : balises `<head>`, JSON-LD, sitemap, robots.txt. Rien ne doit changer dans le rendu visuel de `template.html`. Ce n'est pas une exception à la règle de gel du design — c'est simplement hors de son périmètre (métadonnées non affichées, pas de CSS/HTML de rendu).

## Hypothèse à valider avec François avant de commencer

**Domaine canonique retenu : `https://radar.artisanspei.re/`** (celui utilisé dans toute la communication). `radar.fhservices.re` sert aujourd'hui le même contenu — à traiter comme domaine secondaire (redirection), pas comme canonique. Si François indique l'inverse, inverser partout ci-dessous.

---

## 1. Corriger le contenu dupliqué (deux domaines identiques)

Deux domaines (`radar.fhservices.re` et `radar.artisanspei.re`) servent le même contenu depuis la même app Replit. Pour Google, c'est du contenu dupliqué qui dilue le référencement.

**Choisir une des deux options (la première est préférable si techniquement simple sur Replit) :**

- **Option A — Redirection 301** : configurer `radar.fhservices.re` pour rediriger en 301 vers `https://radar.artisanspei.re/` (au niveau DNS/Replit, ou via une règle serveur dans `server.py` si Replit sert les deux domaines depuis le même déploiement : détecter le `Host` header et rediriger si ce n'est pas le domaine canonique).
- **Option B — Balise canonique** : si on veut garder les deux domaines pleinement fonctionnels sans redirection, ajouter dans `<head>` de `template.html` :
  ```html
  <link rel="canonical" href="https://radar.artisanspei.re/">
  ```
  (et l'équivalent pour `/organisateurs`, avec son propre canonical).

## 2. Sitemap.xml

Créer `/sitemap.xml`, généré par `build.py` (ou servi statiquement par `server.py`), listant au minimum :
```xml
<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://radar.artisanspei.re/</loc><changefreq>daily</changefreq></url>
  <url><loc>https://radar.artisanspei.re/organisateurs</loc><changefreq>weekly</changefreq></url>
</urlset>
```
`changefreq="daily"` sur la page d'accueil est cohérent avec la veille quotidienne à 4h.

## 3. robots.txt

Vérifier/créer `/robots.txt` :
```
User-agent: *
Allow: /
Sitemap: https://radar.artisanspei.re/sitemap.xml
```
Vérifier qu'aucune règle n'exclut accidentellement les pages publiques. Les routes internes (`/admin`, endpoints de soumission du formulaire, webhooks) peuvent être explicitement exclues (`Disallow: /admin`) — elles n'ont rien à faire dans l'index Google et cette page est de toute façon protégée par mot de passe.

## 4. Meta title / description par page

Actuellement une seule balise `<title>` semble couvrir tout le site. Différencier :

- **Page d'accueil** : `<title>Agenda des Exposants — Marchés & Appels à Candidature | La Réunion</title>` + meta description orientée artisan (ex. reprendre le `<p class="sub">` existant, déjà bien écrit).
- **Page `/organisateurs`** : titre et description propres, orientés organisateur (ex. `<title>Proposer un événement — Radar des Marchés Réunion</title>`, description reprenant le sous-titre déjà présent sur cette page).

## 5. Données structurées Event (JSON-LD) — le gain le plus important

Pour chaque événement de `data/events.json` dont le statut n'est pas `closed`, générer dynamiquement (dans `build.py`, au moment de l'injection dans `template.html`) un bloc JSON-LD `schema.org/Event` et l'insérer dans le `<head>` ou en fin de `<body>` de `index.html`. Un bloc par événement, ou un tableau `@graph` regroupant tous les événements sur une page.

Mapping des champs (`events.json` → JSON-LD) :

| Champ `events.json` | Propriété schema.org |
|---|---|
| `name` | `name` |
| `desc` | `description` |
| `place` | `location.name` (type `Place`) |
| `when` / dates dérivées de `month` | `startDate` (format ISO 8601 — si seule une période textuelle est connue et non une date exacte, **omettre `startDate`** plutôt que d'inventer une date : Google pénalise les données structurées incorrectes plus qu'il ne valorise leur absence) |
| `org` | `organizer.name` |
| `url` | `url` |
| — | `eventAttendanceMode`: `https://schema.org/OfflineEventAttendanceMode` |
| — | `eventStatus`: `https://schema.org/EventScheduled` (ou `EventCancelled` si `status == "closed"` et clairement annulé — à ne mettre que si avéré, pas par défaut) |

⚠️ **Règle stricte, cohérente avec le principe « capté ≠ publié » du reste du projet** : ne jamais générer de JSON-LD avec une date inventée ou approximative présentée comme certaine. Si `dateStatus` vaut `"probable"` ou `"à confirmer"`, omettre `startDate` du JSON-LD plutôt que de risquer une pénalité Google pour données structurées trompeuses.

Après implémentation : valider avec le **Rich Results Test** de Google (`search.google.com/test/rich-results`) sur l'URL en production.

## 6. Vérification manuelle (à faire par François, pas par l'agent)

Une fois les points 1 à 5 déployés, François doit :
1. Créer/vérifier la propriété **Google Search Console** pour `radar.artisanspei.re`.
2. Soumettre le sitemap.
3. Utiliser **« Inspection de l'URL »** sur la page d'accueil pour confirmer que le HTML rendu par Google contient bien les noms d'événements (vérifier que le rendu JavaScript fonctionne correctement pour l'indexation — ne pas supposer que ça marche, le confirmer).
4. Tester `/organisateurs` de la même façon.

Documenter ces étapes dans le README (section courte "SEO & Search Console"), avec ce qu'il faut refaire à chaque évolution majeure du site (re-soumettre le sitemap si de nouvelles pages apparaissent, par exemple lors d'une future implémentation de pages individuelles par événement).

## Hors périmètre de ce brief (à traiter plus tard, séparément)

- Pages individuelles par événement (gain SEO plus important mais chantier structurant, voir `Strategie_SEO.md` §4) — brief séparé si/quand François valide cet investissement.
- Page FAQ générée à partir des thèmes du chatbot — également un brief séparé, dépend de l'analyse de thèmes déjà en place (`chat_questions.jsonl`).
- Backlinks / démarchage presse : action humaine de François, pas un chantier technique.
