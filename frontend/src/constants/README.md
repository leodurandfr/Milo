# Shared constants

Module pour les **constantes structurelles partagées entre 2+ modules**
(stores, composables, composants). Ne PAS y placer :

- Données métier (listes de pays, genres musicaux) → fichiers existants
  `countries.js`, `musicGenres.js`, `wifiCountries.js`.
- Constantes utilisées dans un seul module → garder local au module.
- Constantes dérivables du backend (vitesses de lecture, codecs, etc.) →
  fetch depuis l'API au montage et cacher dans le store concerné.

Critère d'acceptation : si la même valeur littérale apparaît dans 2+
fichiers, elle vit ici.
