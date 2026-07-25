---
name: GitHub push auth (radar-marches-reunion)
description: Quelle méthode d'authentification git push fonctionne avec le GITHUB_TOKEN de ce projet
---

# Auth git push vers FHSERVICES974/radar-marches-reunion

**Règle :** pousser via l'URL `https://x-access-token:<TOKEN>@github.com/FHSERVICES974/radar-marches-reunion.git`, avec `credential.helper=` vide, `GIT_ASKPASS` retiré de l'env et `GIT_TERMINAL_PROMPT=0`.

**Why :** le header `http.extraHeader="Authorization: token <TOKEN>"` est refusé par GitHub ("invalid credentials") avec ce token, alors que l'API REST accepte le même token (200, push:true). Seule la forme URL x-access-token fonctionne. De plus, `replit-git-askpass` n'existe pas sur la VM déployée et injecte de mauvais identifiants dans le workspace — il faut le neutraliser explicitement.

**How to apply :** pour tout `git push` (workspace ou code serveur), utiliser la forme URL x-access-token + credential.helper vide + env sans GIT_ASKPASS. Toujours masquer le token dans les sorties d'erreur (`.replace(token, "***")`).
