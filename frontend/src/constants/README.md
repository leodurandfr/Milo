# Shared constants

The home of **structural constants shared by 2+ modules** (stores, composables,
components). Do NOT put here:

- Domain data (country lists, music genres) → the existing files
  `countries.js`, `musicGenres.js`, `wifiCountries.js`.
- Constants used by a single module → keep them local to that module.
- Constants the backend can supply (codecs, presets, etc.) → fetch them from
  the API on mount and cache them in the store concerned.

Acceptance criterion: if the same literal value appears in 2+ files, it lives
here.
