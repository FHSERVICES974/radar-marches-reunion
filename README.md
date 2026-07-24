# Radar Marchés — Agenda des Exposants (Artisans 974)

Système de mise à jour semi-automatique du site **Agenda des Exposants**.
Le **design est figé** : seules les **données** évoluent. Chaque lundi, une veille
**pilotée par Claude** (recherche vérifiée sur sources fiables) prépare une
**proposition** que vous relisez avant de **publier** manuellement.

> Principe directeur : **capté ≠ publié**. La veille ne fait que proposer, avec
> pour chaque item une **source officielle + une date vérifiée + un niveau de
> confiance**. Vous validez ; `publier.py` met en ligne. Mieux vaut 3 appels
> vérifiés que 30 douteux — votre crédibilité en dépend.

```
radar-marches/
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
├── com.fhservices.radar-veille.plist  ← tâche launchd (lundi 7h)
└── README.md
```

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
cd radar-marches
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
Voir `RACCOURCI_IPHONE.md` : un raccourci « Ajouter à Radar » envoie un post
Insta/FB vers `data/inbox_mobile.txt` en un tap, repris par la veille (confiance
« à confirmer »).

### Modifier les données à la main
Éditez `data/events.json` (16 champs — voir schéma dans `veille_agent.md`), puis
`./venv/bin/python build.py`.

## Déploiement GitHub → Replit

**Replit = hébergeur uniquement.** Ne demandez jamais à l'agent Replit de « refaire »
le site (il détruirait le design). GitHub est la source de vérité, Replit importe.

Première fois :
```bash
cd radar-marches
git init && git add . && git commit -m "init radar-marches"
git branch -M main && git remote add origin <URL_GITHUB> && git push -u origin main
```
Puis Replit : **Create Deployment → Import from GitHub**, fichier servi `index.html`.

Redeploy après `publier.py` :
- **Option A (défaut)** : clic « Redeploy » dans l'onglet Deployments (rappelé par publier.py).
- **Option B** : mettez le **Deploy Hook** Replit dans `.env` (`REPLIT_DEPLOY_HOOK=...`)
  → `publier.py` déclenche le redeploy en HTTP automatiquement.

## Tâche automatique (launchd, lundi 7h)
```bash
cp com.fhservices.radar-veille.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.fhservices.radar-veille.plist
# décharger : launchctl unload ~/Library/LaunchAgents/com.fhservices.radar-veille.plist
```
Le plist appelle `run_veille.sh` (agent Claude headless) et journalise `veille.log`.

> ⚠️ **iCloud Drive** : le projet est sous iCloud. Pour que launchd lise les
> fichiers, gardez le dossier « Toujours garder sur ce Mac », ou déplacez-le hors
> d'iCloud (ex. `~/radar-marches`) et ajustez les chemins dans le `.plist` et
> `run_veille.sh`.

## Non-régression du design
Après tout build, `index.html` doit rendre à l'identique de
`reference_dashboard-artisans-reunion.html` (données égales). Garanti par
construction : `build.py` ne touche que les 3 placeholders.
