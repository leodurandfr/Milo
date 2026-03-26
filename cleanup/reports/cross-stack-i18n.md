# i18n Key Usage Report: `english.json` vs `.vue` Files

**Date:** 2026-03-26
**Source:** `frontend/src/locales/english.json`
**Searched in:** All `.vue` files under `frontend/src/`

---

## Summary

| Metric | Count |
|--------|-------|
| Total i18n keys (leaf nodes) | **233** |
| Keys referenced in `.vue` files | **195** |
| Keys never referenced in any `.vue` file | **17** |

> Note: 1 key (`status.audioReceivedFrom`) is used in a `.js` composable but not in any `.vue` file.

---

## Unused Keys (Never Referenced in Any `.vue` File)

### `audioSources.cdSource` — 4 unused keys

| Key | Value | Notes |
|-----|-------|-------|
| `audioSources.cdSource.insertDisc` | "Insert a CD" | No template renders this label |
| `audioSources.cdSource.noDisc` | "No disc" | No template renders this label |
| `audioSources.cdSource.eject` | "Eject" | Eject button uses icon only |
| `audioSources.cdSource.tracklist` | "Tracklist" | No template renders this label |

### `status` — 1 unused key

| Key | Value | Notes |
|-----|-------|-------|
| `status.audioReceivedFrom` | "Audio received from" | Used only in `useScreensaver.js` (a `.js` composable), not in any `.vue` file |

### `network` — 2 unused keys

| Key | Value | Notes |
|-----|-------|-------|
| `network.connectedTo` | "Connected to" | Not referenced in any template |
| `network.saved` | "Saved" | Not referenced in any template |

### `podcasts` — 4 unused keys

| Key | Value | Notes |
|-----|-------|-------|
| `podcasts.daysAgo` | "{count} days ago" | `EpisodeCard.vue` uses `Intl.DateTimeFormat` instead |
| `podcasts.weeksAgo` | "{count} weeks ago" | Same — native browser API used |
| `podcasts.monthsAgo` | "{count} months ago" | Same — native browser API used |
| `podcasts.yearsAgo` | "{count} years ago" | Same — native browser API used |

### `setup` — 6 unused keys

| Key | Value | Notes |
|-----|-------|-------|
| `setup.mode.title` | "Choose your mode" | Entire `setup.mode` block is dead — no mode selection step exists |
| `setup.mode.server` | "Milo OS" | Same |
| `setup.mode.serverDescription` | "Operating system with audio sources..." | Same |
| `setup.mode.client` | "Milo Client" | Same |
| `setup.mode.clientDescription` | "Multiroom receiver..." | Same |
| `setup.summary.mode` | "Mode" | References removed mode selection |

---

## Unused Keys by Namespace

| Namespace | Unused Count | Total Keys | Notes |
|-----------|-------------|------------|-------|
| `audioSources.cdSource` | 4 | 7 | CD labels prepared but not rendered |
| `status` | 1 | 16 | Used in `.js` only |
| `network` | 2 | ~15 | Dead labels |
| `podcasts` | 4 | ~35 | Replaced by `Intl` API |
| `setup` | 6 | ~15 | Removed mode selection step |
| **Total** | **17** | **233** | |

---

## Notable Findings

### 1. `setup.mode.*` — Entire Sub-Namespace Dead
The 5-key `setup.mode` block and `setup.summary.mode` correspond to a mode-selection step (Server vs. Client) that was planned or removed. No Vue component references them.

### 2. `podcasts.daysAgo/weeksAgo/monthsAgo/yearsAgo` — Replaced by Native API
`EpisodeCard.vue` uses the browser's `Intl.DateTimeFormat` for dates older than 1 day instead of these i18n keys. The keys are unreachable.

### 3. `audioSources.cdSource` — Labels Prepared but Not Rendered
The CD source uses icon-only buttons for eject and tracklist. The text labels `insertDisc`, `noDisc`, `eject`, and `tracklist` exist in the locale file but are never rendered.

### 4. `status.audioReceivedFrom` — Used Outside Vue
This key is called from `frontend/src/composables/useScreensaver.js` but not from any `.vue` template directly.

### 5. Duplicate Genre Keys in `podcasts.genres`
Both camelCase and snake_case versions exist for two genres:
- `podcasts.genres.trueCrime` (used in `HomeView.vue`) + `podcasts.genres.true_crime` (used in `SearchView.vue`)
- `podcasts.genres.health` (used in `HomeView.vue`) + `podcasts.genres.health_and_fitness` (used in `SearchView.vue`)

Both spellings are referenced — not dead, but a consistency issue.
