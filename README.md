# Radar Marchés — Agenda des Exposants (Artisans 974)

Système de mise à jour semi-automatique du site **Agenda des Exposants**.
Le **design est figé** : seules les **données** évoluent. Chaque lundi, une veille
**pilotée par Claude** (recherche vérifiée sur sources fiables) prépare une
**proposition** que vous relisez avant de **publier** manuellement.

> Principe directeur : **capté ≠ publié**. La veille ne fait que proposer, avec
> pour chaque item une **source officielle + une date vérifiée + un niveau de
> confiance**. Vous validez ; `publier.py` met en ligne. Mieux vaut 3 appels
> vérifiés que 30 douteux — votre crédibilité en dépend.

> 📍 **Emplacement : `/Users/fhubert/Claude/radarartisans`** (hors iCloud, depuis
> le 25/07/2026). Le projet était sous iCloud Drive : la protection de
> confidentialité de macOS y bloquait launchd (`can't open input file:
> ./run_veille.sh`, sortie 127) — le cron ne tournait jamais. Ne pas le remettre
> dans iCloud. Les chemins sont en dur dans `run_veille.sh`, `run_weekly.sh`,
> `ingest_docs.sh` et le `.plist` : un déplacement impose de les mettre à jour
> **et** de recréer le `venv` (non relocalisable).

```
radarartisans/
├── data/
│   ├── events.json          ← source de vérité : les événements
│   ├── orgs.json            ← répertoire des organisateurs
│   ├── meta.json            ← { "lastUpdate": "AAAA-MM-JJ" }
│   ├── sources.json         ← REGISTRE des sources balayées (4 niveaux)
│   ├── community_inbox.json ← remontées manuelles (Insta/FB, réseau d'artisans)
│   ├── backups/             ← sauvegardes horodatées (gitignored)
│   └── pending/             ← propositions machine à curer (gitignored)
├── template.html            ← LE DESIGN (copie exacte + 3 placeholders)
├── index.html               ← généré par build.py — page servie par Replit
├── build.py                 ← injecte JSON → index.html
├── status_check.py          ← recalcul déterministe des statuts (dates)
├── veille_agent.md          ← PLAYBOOK de l'agent de veille (le "cerveau")
├── run_veille.sh            ← lance l'agent Claude en headless (appelé lundi 7h)
├── publier.py               ← valide + build + git push + redeploy Replit
├── common.py                ← helpers partagés
├── com.fhservices.radar-veille.plist  ← tâche launchd (quotidien 4h, phase de lancement)
└── README.md
```

> ⚠️ **`server.py` (widget de chat + `/admin`) est écrit par l'agent Replit**, pas
> par ce pipeline. Il est bien versionné ici depuis la fusion du 25/07/2026 —
> mais toute modification doit passer par Replit, jamais éditée à la main.
>
> 🚨 **Piège corrigé le 25/07/2026, à ne pas réintroduire** : le widget de chat
> n'existait que dans `index.html`. Comme `build.py` régénère `index.html` depuis
> `template.html`, la moindre publication (dont la veille quotidienne de 4h)
> l'aurait effacé du site. Le widget est désormais **dans `template.html`**.
> Règle : *tout* ce qui doit apparaître sur le site public vit dans
> `template.html` — si l'agent Replit ajoute quelque chose directement à
> `index.html`, il faut le reporter dans le template, sinon c'est perdu au
> prochain build.

## 🔒 Règle d'or (design intouchable)

`template.html` contient le CSS/HTML/JS **à l'identique** de l'original. `build.py`
n'y remplace que 3 placeholders (`__EVENTS__`, `__ORGS__`, `__LASTUPDATE__`). Le
rendu reste **strictement identique** à `reference_dashboard-artisans-reunion.html`
tant que les données ne changent pas. On n'édite jamais `index.html` à la main.

## Comment fonctionne la veille (architecture qualité)

La veille n'est **pas** un scraper à mots-clés. C'est un **agent Claude** qui,
chaque lundi :

1. Lance `status_check.py` (maths de dates : deadlines dépassées, éditions passées).
2. **Balaie `data/sources.json`** — 4 niveaux, du plus fiable au plus large :
   - **Tier 1 institutionnel** : CMA, Département, mairies (avis de publicité /
     appel à forains), TCO (+ port de plaisance St-Gilles), Nou Lé Lokal — **+ les
     24 communes** de l'île (petites incluses).
   - **Tier 2 lieux/hôtes** : Jardin d'Eden, Domaine des Tourelles, Nordev, hôtels,
     villages artisanaux, espaces événementiels.
   - **Tier 2b organismes privés d'événementiel** : agences qui lancent leurs appels.
   - **Tier 3 agrégateurs/presse** (découverte) : IRT `reunion.fr`, guide-reunion,
     offices de tourisme, zinfos974, imazpress, linfo, clicanoo, flanerbouger.
   - **Tier 4 réseaux sociaux** : ~20 comptes cibles.
3. **Ouvre et LIT chaque page** pour vérifier date, deadline, lien officiel.
4. Classe : NOUVEAU / MISE À JOUR / STATUT, avec confiance **Vérifié / Probable /
   À confirmer**. Filtre géo 974 (attention homonymes métropole).
5. Écrit `proposition_MAJ_AAAA-MM-JJ.md` (lisible) + `data/pending/pending_*.json`
   (machine). **N'écrit jamais `events.json`.** Notifie macOS.

Pour ajouter/retirer des sources : éditez simplement `data/sources.json`.

### ⚠️ Les réseaux sociaux (Facebook / Instagram) — à savoir
Un robot ne peut pas se connecter à FB/IG (login + CGU). La veille les couvre en
**3 couches** : (1) recherche indirecte des posts publics indexés ; (2) préférence
à la source officielle `.re` qui recopie souvent l'appel ; (3) **capture humaine**
via `community_inbox.json` — pour les marchés qui n'existent QUE sur Insta, un
humain qui suit les comptes reste le moyen le plus fiable. Suivez vos comptes
cibles depuis un compte dédié, activez les notifications, et déposez les liens.

## Utilisation

### Installation
```bash
cd ~/Claude/radarartisans
python3 -m venv venv && ./venv/bin/pip install -r pipeline-requirements.txt
cp .env.example .env     # REPLIT_DEPLOY_HOOK (option B) ; Brave n'est plus requis
```
Prérequis veille : le CLI `claude` installé et authentifié (déjà le cas si vous
utilisez Claude Code).

### Lancer la veille à la main (test)
```bash
./run_veille.sh          # exécute l'agent, écrit la proposition, journalise veille.log
```

### Relire & publier
1. Lisez `proposition_MAJ_AAAA-MM-JJ.md`.
2. Ouvrez `data/pending/pending_MAJ_AAAA-MM-JJ.json` : gardez les `status_changes`
   validés ; pour un nouvel appel, le champ `event` est déjà rempli (items
   « Vérifié ») ou à compléter (items « Probable »).
3. Publiez :
   ```bash
   ./venv/bin/python publier.py --apply data/pending/pending_MAJ_AAAA-MM-JJ.json
   ```
   → backup events.json · applique · `lastUpdate`=aujourd'hui · build · git push · redeploy.

   Sans nouvel appel (juste rafraîchir la date) : `./venv/bin/python publier.py`
   Options : `--no-push` (test local), `--set-date AAAA-MM-JJ`.

### Publication automatique — Niveau 1 (« sans intervention », plafonnée)
`publier.py --auto` publie **tout seul** la partie sûre du dernier pending :
- **changements de statut** (déterministes, sans scraping) ;
- **nouveaux appels « Vérifié »** dont la **source est un domaine institutionnel**
  du registre (mairies/CMA/TCO/…), dans la limite de `AUTO_MAX` (défaut 5).

Tout le reste (Probable / lieux privés / presse / **réseaux sociaux**) reste en
attente de votre validation. Garde-fous : whitelist de sources, plafond
anti-anomalie, jamais de suppression/modif (ajouts seulement), backup + notif.

```bash
./venv/bin/python publier.py --auto        # auto-publie la part sûre + push + redeploy
./venv/bin/python publier.py --rollback    # annule : restaure le dernier backup + rebuild
```
**Cycle hebdo complet** (`run_weekly.sh` = veille puis `--auto`) : pour l'activer,
pointez le `.plist` sur `run_weekly.sh` au lieu de `run_veille.sh`. Ne devient
réellement « en ligne sans intervention » qu'une fois **git push non interactif**
+ **REPLIT_DEPLOY_HOOK** configurés (voir Déploiement).

### Documents reçus (PDF, docx, photos) — zéro blocage
Déposez tout appel reçu (mail, scan, **photo d'un flyer**, formulaire) dans
`data/inbox_docs/`, puis `./ingest_docs.sh` — l'agent lit le document (PDF scanné
et images lus **visuellement**), en extrait l'événement vérifié et le propose. La
veille du lundi traite aussi ce dossier automatiquement.

### Réseaux sociaux depuis l'iPhone
Raccourci « Ajouter à Radar » : un post Insta/FB partagé en un tap s'ajoute à une
note Apple dédiée **« Radar Inbox »** (synchronisée iCloud). `run_veille.sh`
exporte cette note en AppleScript natif (`osascript`, sans dépendance MCP — le
CLI `claude -p` headless n'a pas accès aux serveurs MCP des sessions interactives)
vers `data/inbox_mobile_export.txt` avant de lancer l'agent, puis archive et vide
la note pour la semaine suivante. Confiance « à confirmer » sauf recoupement.
> Note : `RACCOURCI_IPHONE.md` décrit une ancienne variante (fichier texte direct)
> et est obsolète — le mécanisme réel est celui ci-dessus, basé sur Notes.

### Modifier les données à la main
Éditez `data/events.json` (16 champs — voir schéma dans `veille_agent.md`), puis
`./venv/bin/python build.py`.

## Déploiement GitHub → Replit

**Replit = hébergeur uniquement.** Ne demandez jamais à l'agent Replit de « refaire »
le site (il détruirait le design). GitHub est la source de vérité, Replit importe.

Première fois :
```bash
cd ~/Claude/radarartisans
git init && git add . && git commit -m "init radar-marches"
git branch -M main && git remote add origin <URL_GITHUB> && git push -u origin main
```
Puis Replit : **Create Deployment → Import from GitHub**, fichier servi `index.html`.

Redeploy après `publier.py` :
- **Webhook `/sync` (actif)** : un webhook GitHub → Replit (sécurisé HMAC via
  `GITHUB_WEBHOOK_SECRET`) redéploie automatiquement `index.html` à chaque push
  sur `main`. Aucune action manuelle nécessaire pour les mises à jour de données.
- **Publish (manuel, à utiliser avec précaution)** : l'action « Publish » de
  Replit remplace **toute la VM de production** par l'état du workspace — utile
  pour déployer une évolution du `server.py` (widget, `/admin`…), mais elle peut
  écraser des données GitHub plus récentes que le workspace si celui-ci n'a pas
  été resynchronisé au préalable. Toujours vérifier que le workspace est à jour
  avant de publier.
- **Option historique** : Deploy Hook HTTP (`REPLIT_DEPLOY_HOOK` dans `.env`),
  conservée par `publier.py` en repli si le webhook `/sync` est indisponible.

## Assistant IA « Le ti artisan futé »

Widget de chat flottant sur le site public, géré côté `server.py` (Replit) :
- **Grounding** : s'appuie d'abord sur les données du site (`events.json`/`orgs.json`),
  puis sur une recherche web restreinte aux domaines officiels de confiance (CMA,
  service-public, impots.gouv, etc.) pour les questions générales d'artisan
  (statut, fiscalité, démarches).
- **Modèles** : routage à deux niveaux — Haiku par défaut, escalade vers Sonnet
  si la question est plus complexe/poussée.
- **Ton** : professionnel et convivial, pensé pour un public peu à l'aise avec l'IA.
- **Limite** : 20 messages/heure par visiteur.
- Historique des questions loggé (`data/chat_questions.jsonl` côté VM production)
  pour alimenter l'analyse de thèmes du tableau de bord `/admin` (voir ci-dessous).

## Tableau de bord privé `/admin`

Page de statistiques **non publique**, réservée au propriétaire :
- **Authentification par mot de passe** (secret Replit `ADMIN_PASSWORD`, cookie de
  session signé HMAC-SHA256, `HttpOnly`/`Secure`/`SameSite=Strict`, 12h). L'auth
  Replit native (header `X-Replit-User-Name`) avait été essayée en premier mais
  souffrait d'un bug de redirection (`replit.com/login` sans retour vers `/admin`)
  et a été remplacée par ce mot de passe, choix confirmé par le propriétaire.
- **Trafic** : visites/visiteurs uniques (IP hachée, jamais stockée en clair),
  répartition par source de référent (direct / Google / Facebook / Instagram /
  WhatsApp / autre).
- **Thèmes du chatbot** : analyse hebdomadaire par Claude des questions posées à
  « Le ti artisan futé », pour identifier les besoins récurrents des artisans et
  orienter les évolutions du site. Se déclenche automatiquement dès 3 questions
  enregistrées, puis toutes les 7 jours.
- Design premium dédié (graphiques), indépendant du design figé du site public —
  cette page n'est pas soumise à la règle d'or ci-dessus.

## Tâche automatique (launchd, chaque jour 4h — quotidien pendant le lancement)

> Fréquence actuelle : **tous les jours**, le temps du lancement du site (les
> appels à candidature arrivent au fil de l'eau). Repasser à hebdomadaire plus
> tard en ajoutant `<key>Weekday</key><integer>1</integer>` (lundi) dans
> `StartCalendarInterval` du `.plist`, puis recopier dans
> `~/Library/LaunchAgents/` et recharger (`launchctl unload` puis `load`).
```bash
cp com.fhservices.radar-veille.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.fhservices.radar-veille.plist
# décharger : launchctl unload ~/Library/LaunchAgents/com.fhservices.radar-veille.plist
```
Le plist appelle `run_veille.sh` (agent Claude headless) et journalise `veille.log`.

### Deux pièges launchd déjà rencontrés (et corrigés)
1. **iCloud Drive** — le projet y était : launchd ne pouvait pas lire
   `run_veille.sh` (`can't open input file`, sortie 127) à cause de la protection
   de confidentialité macOS. Le cron n'a jamais tourné tant que c'était le cas.
   → Résolu en déplaçant le projet vers `~/Claude/radarartisans`.
2. **PATH minimal** — launchd ne fournit que `/usr/bin:/bin:/usr/sbin:/sbin`, où
   le CLI `claude` (installé via npm dans `~/.npm-global/bin`) est absent :
   `command not found`, sortie 127 à nouveau. → Résolu en résolvant le binaire
   explicitement dans `run_veille.sh` et `ingest_docs.sh`.

Pour tester le cron sans attendre 4h (exécution réelle via launchd) :
```bash
launchctl kickstart -p gui/$(id -u)/com.fhservices.radar-veille
```

## Non-régression du design
Après tout build, `index.html` doit rendre à l'identique de
`reference_dashboard-artisans-reunion.html` (données égales). Garanti par
construction : `build.py` ne touche que les 3 placeholders.
