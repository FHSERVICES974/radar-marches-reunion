# Agenda des Exposants — Artisans de La Réunion

Page web statique listant tous les marchés, foires, salons et appels à candidature pour les artisans, créateurs et producteurs de l'île de La Réunion.

## Stack

- HTML / CSS / JavaScript (un seul fichier `index.html`, aucune dépendance)
- Serveur de développement : Python `http.server`

## Lancer le projet

```bash
python3 -m http.server 5000
```

La page est alors accessible sur le port 5000.

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
