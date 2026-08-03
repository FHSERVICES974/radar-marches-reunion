# Brief Claude Code — Test de la veille après ajout du calendrier saisonnier

> **Court chantier de vérification.** Deux fichiers viennent d'être modifiés à la main
> (hors Claude Code). Il faut s'assurer que la veille quotidienne tourne toujours
> correctement avant la prochaine exécution automatique de 4h.

---

## Ce qui a été modifié le 3 août 2026

### 1. `data/veille_calendrier.json` — **nouveau fichier**

Calendrier de surveillances **saisonnières** : sources dont l'appel à candidature
n'est en ligne que quelques semaines par an. Hors de leur fenêtre, la page est
absente, périmée, ou affiche l'édition précédente.

Structure :
```
{
  "_doc": "...",
  "_mode_emploi_agent": [ ... ],
  "surveillances": [
    {
      "nom": "...",
      "zone": "...",
      "url": "...",
      "url_modele": "...{annee}",
      "mois_de_veille": [12, 1, 2],
      "priorite": "haute",
      "pourquoi": "...",
      "historique_connu": { ... },
      "a_verifier_dans_l_appel": [ ... ],
      "contact_repli": "...",
      "angle_mort_connu": "...",
      "note": "...",
      "derniere_verification": "AAAA-MM-JJ",
      "statut_edition_courante": "..."
    }
  ]
}
```

Une seule entrée pour l'instant : **Ville de Saint-Denis — appel à forains**
(fenêtre décembre-janvier-février). Un appel annuel unique y ouvre l'accès à toutes
les manifestations de la ville : Marché de Nuit, Marché de Bancoul, Braderie de
l'Océan, Journées commerciales, Dimanche ô Barachois, fêtes calendaires. Rater cette
fenêtre fait perdre une année entière aux artisans du Nord.

### 2. `veille_agent.md` — **3 lignes ajoutées**

Dans la section « Entrées à lire d'abord », après `data/community_inbox.json` :
l'agent doit désormais lire `data/veille_calendrier.json`, comparer le mois courant
aux `mois_de_veille`, et **signaler les fenêtres ouvertes en section 6 du rapport même
quand il ne trouve rien**.

> C'est le point important : aujourd'hui une absence de résultat est silencieuse,
> donc indistinguable d'une panne. Une fenêtre ouverte doit produire une ligne dans
> le rapport, quoi qu'il arrive.

---

## Ce qu'il faut faire

### Étape 1 — Vérifier l'intégrité

- `data/veille_calendrier.json` est un JSON valide et se parse correctement.
- La modification de `veille_agent.md` est cohérente avec le reste du playbook (pas
  de doublon, pas de contradiction avec les règles existantes).
- Aucun autre fichier n'a été touché par erreur.

### Étape 2 — Lancer une veille manuelle complète

Exécuter le cycle de veille normal, comme le fait `run_veille.sh` à 4h, et
**observer** :

- L'agent lit-il bien le nouveau fichier sans erreur ?
- Le mois courant (août = 8) n'étant dans **aucune** fenêtre, l'entrée Saint-Denis
  doit être **ignorée silencieusement** — aucune mention dans le rapport. C'est le
  comportement attendu, pas un bug.
- Le reste du déroulé (statuts, inbox, tiers 1 à 4, rapport, pending) fonctionne
  comme avant.

### Étape 3 — Tester le déclenchement de la fenêtre

Le comportement en fenêtre ouverte ne se testera pas naturellement avant décembre.
Le vérifier maintenant, par l'un de ces moyens :

- ajouter temporairement `8` aux `mois_de_veille` de l'entrée Saint-Denis, relancer,
  vérifier que la source est traitée en priorité et signalée en section 6, **puis
  retirer le 8** ;
- ou simuler la comparaison de dates dans un test isolé.

**Résultat attendu en fenêtre ouverte** : la source est ouverte en priorité, et le
rapport contient une ligne du type *« Saint-Denis appel à forains — fenêtre de veille
ouverte, page consultée, rien de publié à ce jour »*, ou le contenu trouvé.

⚠️ Le site `saintdenis.re` **bloque fréquemment l'accès automatisé** (réponse
« Attack detected »). C'est documenté dans le champ `angle_mort_connu`. En cas de
blocage, l'agent ne doit pas insister : le signaler en section 6 et recommander une
vérification manuelle. Ce cas doit être testé aussi — c'est le plus probable.

### Étape 4 — Vérifier l'état du dépôt

`veille_agent.md` a une modification locale non commitée. À committer avec le nouveau
fichier, message suggéré :

```
Veille : ajout d'un calendrier de surveillances saisonnières

- data/veille_calendrier.json : sources à fenêtre étroite (1re entrée : appel à
  forains Saint-Denis, décembre-février)
- veille_agent.md : lecture du calendrier + signalement des fenêtres ouvertes en
  section 6, même sans résultat
```

⚠️ Vérifier au passage que le dépôt local est bien synchronisé avec la production.
Un écart a déjà été constaté le 30 juillet (`events.json` local en retard d'un jour
sur le site en ligne). Faire un `git pull` avant tout travail sur ce dépôt.

### Étape 5 — Vérifier que le cron tourne toujours

Le site affichait « Dernière mise à jour : 1 août » le 3 août au matin. Soit la veille
des 2 et 3 août n'a rien trouvé de publiable — ce qui est plausible un week-end —
soit le job `launchd` ne s'est pas exécuté.

Vérifier `veille.log` et `launchd.err.log`, et confirmer que
`com.fhservices.radar-veille.plist` est bien chargé et programmé.

---

## Ce qu'il ne faut PAS faire

- **Ne pas ajouter d'autres entrées** au calendrier de votre propre initiative. Le
  fichier est conçu pour grandir, mais chaque surveillance doit reposer sur une
  fenêtre réellement observée, pas supposée. François validera les ajouts.
- **Ne pas modifier `data/events.json`** — la règle « capté ≠ publié » reste entière.
- **Ne pas restructurer** `veille_agent.md` au-delà de la vérification de cohérence.

---

## À rendre

Un compte rendu court :

1. la veille tourne-t-elle sans erreur avec le nouveau fichier ?
2. le comportement hors fenêtre est-il bien silencieux ?
3. le comportement en fenêtre ouverte produit-il bien une ligne en section 6 ?
4. que donne `saintdenis.re` — accessible, ou bloqué ?
5. le cron de 4h est-il actif ?

Et, s'il y a lieu, ce qu'il faudrait corriger.
