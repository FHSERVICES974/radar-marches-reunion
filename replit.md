# Agenda des Exposants — Artisans de La Réunion

Page web statique listant tous les marchés, foires, salons et appels à candidature pour les artisans, créateurs et producteurs de l'île de La Réunion.

## Stack

- HTML / CSS / JavaScript (un seul fichier `index.html`, aucune dépendance)
- Serveur : `server.py` (Python stdlib — sert les fichiers statiques + webhook GitHub)

## Lancer le projet

```bash
python3 server.py
```

La page est accessible sur le port 5000.

## Synchronisation automatique GitHub → Replit

Chaque push sur la branche `main` du dépôt GitHub déclenche automatiquement un `git pull` sur Replit via un webhook sécurisé.

### Comment ça marche

1. `server.py` expose un endpoint `POST /sync`.
2. GitHub envoie une requête signée à cet endpoint après chaque push.
3. La signature HMAC-SHA256 est vérifiée avec le secret `GITHUB_WEBHOOK_SECRET` (Replit Secret).
4. Si le push est sur `main`, `git pull origin main` est exécuté automatiquement.

### Pourquoi le déploiement est VM (et non autoscale)

En déploiement autoscale, Replit fournit un snapshot du code sans dépôt git.
La commande `git pull` échoue avec `fatal: not a git repository`.

En déploiement **VM** :
- Le serveur tourne en continu dans un conteneur persistant.
- `scripts/start.sh` initialise automatiquement le dépôt git depuis GitHub au démarrage.
- Le webhook `/sync` peut ensuite faire `git pull` normalement.

### Configurer le webhook GitHub (à faire une seule fois)

1. Aller sur **GitHub → votre dépôt → Settings → Webhooks → Add webhook**
2. **Payload URL** : `https://radar.fhservices.re/sync`  
   *(URL de production fixe — ne change jamais)*
3. **Content type** : `application/json`
4. **Secret** : la valeur du secret `GITHUB_WEBHOOK_SECRET` que vous avez définie dans Replit
5. **Which events** : cocher *Just the push event*
6. Cliquer **Add webhook**

> ✅ L'application est déployée en **VM** sur `https://radar.fhservices.re`. Cette URL est permanente et n'a pas besoin d'être reconfigurée.

## Personnalisation

Dans `index.html`, la constante `CONTACT` contient le numéro WhatsApp, l'email et le lien Cal.com à adapter :

```js
const CONTACT = {
  whatsapp: "262692678751",
  email:    "shadowneox@gmail.com",
  cal:      "https://cal.com/fhservices/45min"
};
```

Les événements sont définis dans le tableau `EVENTS` (même fichier).

## Préférences utilisateur

- Design clair et moderne (jamais de thème sombre)
- Changements minimaux — ne pas réécrire les fichiers entiers sans proposer d'abord
