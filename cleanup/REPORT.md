# Codebase Cleanup Report

**Generated:** 2026-03-26
**Sources:** vulture, ruff, eslint, knip, manual cross-stack analysis, shell audit, systemd audit

---

## 1. Dead Code

### 1.1 Python -- Unused Methods & Properties (41 items)

Grouped by file, sorted by estimated lines removable.

#### `backend/core/multiroom/client_registry.py` (~80 lines)

| Line | Symbol | Tag |
|------|--------|-----|
| 712 | `set_zone_clients()` | `[REMOVE]` |
| 985 | `get_zone_ids()` | `[REMOVE]` |
| 1092 | `get_client_equalizer_settings()` | `[REMOVE]` |
| 1156 | `get_state_dict()` | `[REMOVE]` |
| 1170 | `unsubscribe()` | `[REMOVE]` |

#### `backend/core/volume/service.py` (~60 lines)

| Line | Symbol | Tag |
|------|--------|-----|
| 411 | `sync_existing_client_from_snapcast()` | `[REMOVED]` -- confirmed dead, logic moved to websocket._sync_reconnecting_client_volume() |
| 459 | `sync_client_volume_from_external()` | `[REMOVED]` -- confirmed dead, callers use update_client_volume_db() directly |
| 850 | `update_client_availability()` | `[REMOVED]` -- confirmed dead, websocket uses registry.set_client_online() |

#### `backend/core/volume/equalizer_controller.py` (~50 lines)

| Line | Symbol | Tag |
|------|--------|-----|
| 54 | `set_router()` | `[REMOVE]` |
| 217 | `sync_all_from_hardware()` | `[REMOVE]` |
| 254 | `set_timeout()` | `[REMOVE]` |

#### `backend/core/volume/state.py` (~40 lines)

| Line | Symbol | Tag |
|------|--------|-----|
| 320 | `get_startup_volume()` | `[REMOVE]` |
| 833 | `get_volume_limits()` | `[REMOVE]` |

#### `backend/core/equalizer/sync.py` (~50 lines)

| Line | Symbol | Tag |
|------|--------|-----|
| 148 | `cleanup_duplicate_clients()` | `[REMOVE]` |
| 297 | `sync_settings()` | `[REMOVE]` |
| 429 | `apply_standalone_settings_to_client()` | `[REMOVE]` |

#### `backend/core/equalizer/service.py` (~20 lines)

| Line | Symbol | Tag |
|------|--------|-----|
| 859 | `clear_active_preset()` | `[REMOVE]` |

#### `backend/core/multiroom/snapcast.py` (~30 lines)

| Line | Symbol | Tag |
|------|--------|-----|
| 94 | `set_client_name()` | `[REMOVE]` |
| 224 | `get_detailed_clients()` | `[REMOVE]` |

#### `backend/core/multiroom/crossover.py` (~20 lines)

| Line | Symbol | Tag |
|------|--------|-----|
| 564 | `on_zone_changed()` | `[REMOVE]` |

#### `backend/core/multiroom/routing.py` (~15 lines)

| Line | Symbol | Tag |
|------|--------|-----|
| 694 | `get_available_services()` | `[REMOVE]` |

#### `backend/core/systemd.py` (~15 lines)

| Line | Symbol | Tag |
|------|--------|-----|
| 28 | `enable()` | `[REMOVED]` -- confirmed dead, install scripts use systemctl directly |
| 32 | `disable()` | `[REMOVED]` -- confirmed dead, install scripts use systemctl directly |
| 127 | `set_hostname()` | `[REMOVE]` |

#### `backend/sources/podcast/taddy_api.py` (~50 lines)

| Line | Symbol | Tag |
|------|--------|-----|
| 777 | `get_multiple_podcast_series()` | `[REMOVE]` |
| 823 | `get_multiple_episodes()` | `[REMOVE]` |
| 955 | `clean_expired_cache()` | `[REMOVE]` |

#### `backend/sources/spotify/source.py` (~15 lines)

| Line | Symbol | Tag |
|------|--------|-----|
| 521 | property `api_url` | `[REMOVE]` |
| 526 | property `device_connected` | `[REMOVE]` |
| 531 | property `has_active_session` | `[REMOVE]` |

#### `backend/sources/airplay/source.py` (~5 lines)

| Line | Symbol | Tag |
|------|--------|-----|
| 338 | property `device_connected` | `[REMOVE]` |

#### `backend/sources/radio/source.py` (~10 lines)

| Line | Symbol | Tag |
|------|--------|-----|
| 504 | property `mpv` | `[REMOVE]` |
| 519 | property `current_station` | `[REMOVE]` |

#### `backend/sources/radio/data.py` (~10 lines)

| Line | Symbol | Tag |
|------|--------|-----|
| 288 | `get_favorites()` | `[REMOVED]` -- confirmed dead, frontend uses `/stations?favorites_only=true` instead |

#### `backend/sources/radio/shazam.py` (~5 lines)

| Line | Symbol | Tag |
|------|--------|-----|
| 83 | `clear_track()` | `[REMOVE]` |

#### `backend/sources/podcast/source.py` (~5 lines)

| Line | Symbol | Tag |
|------|--------|-----|
| 595 | property `mpv` | `[REMOVE]` |

#### `backend/hardware/service.py` (~10 lines)

| Line | Symbol | Tag |
|------|--------|-----|
| 263 | `reload()` | `[REMOVE]` |

#### `backend/dependencies.py` (~10 lines)

| Line | Symbol | Tag |
|------|--------|-----|
| 32 | `reset_services()` | `[REMOVED]` -- confirmed dead, no test or production usage |

### 1.2 Python -- Unused Constants & Variables

#### `backend/config/constants.py` (~10 lines)

| Line | Symbol | Tag |
|------|--------|-----|
| 19 | `LAST_VOLUME_FILE` | `[REMOVED]` -- confirmed dead, path hardcoded in volume/state.py |
| 20 | `RADIO_DATA_FILE` | `[REMOVED]` -- confirmed dead, path hardcoded in radio/data.py |
| 21 | `PODCAST_DATA_FILE` | `[REMOVED]` -- confirmed dead, path hardcoded in podcast/data.py |
| 23 | `ROUTING_ENV_FILE` | `[REMOVED]` -- confirmed dead, path hardcoded in routing.py |
| 29 | `RADIO_IMAGES_DIR` | `[REMOVED]` -- confirmed dead, path hardcoded in radio/data.py |
| 47 | `CLIENT_REQUEST_TIMEOUT` | `[REMOVE]` -- also unused import in client_proxy.py |
| 53 | `MAC_RTP_PORT` | `[RESOLVED]` -- alive via `_const()` dynamic access in dependencies.py |
| 54 | `MAC_RS8M_PORT` | `[RESOLVED]` -- alive via `_const()` dynamic access in dependencies.py |
| 55 | `MAC_RTCP_PORT` | `[RESOLVED]` -- alive via `_const()` dynamic access in dependencies.py |
| 56 | `MAC_AUDIO_OUTPUT` | `[RESOLVED]` -- alive via `_const()` dynamic access in dependencies.py |

#### Module-level constants

| File | Line | Symbol | Tag |
|------|------|--------|-----|
| `backend/core/equalizer/service.py` | 52 | `COMMAND_TIMEOUT` | `[REMOVE]` |
| `backend/core/equalizer/sync.py` | 35 | `SYNC_CATEGORIES` | `[REMOVE]` |

#### Unused attributes

| File | Line | Symbol | Tag |
|------|------|--------|-----|
| `backend/core/equalizer/service.py` | 79 | `_current_config` | `[REMOVE]` |
| `backend/sources/podcast/taddy_api.py` | 158 | `_itunes_lookup_cache` | `[REMOVE]` |

#### Unused local variables (source code only)

| File | Line | Symbol | Tag |
|------|------|--------|-----|
| `backend/api/settings.py` | 859 | `temp_stderr` | `[REMOVE]` |
| `backend/api/settings.py` | 865 | `throttle_stderr` | `[REMOVE]` |
| `backend/core/multiroom/websocket.py` | 602-603 | `client_id`, `client_name` | `[REMOVE]` |
| `backend/sources/podcast/source.py` | 519 | `position_changed` | `[REMOVE]` |
| `backend/sources/radio/data.py` | 705 | `success` | `[REMOVE]` |
| `backend/sources/bluetooth/agent.py` | 127 | `entered` | `[REMOVE]` |
| `backend/sources/podcast/routes.py` | 216 | `l` (ambiguous name) | `[REFACTOR]` -- rename to descriptive name |

### 1.3 Python -- Unused Imports (21 in source, auto-fixable)

All auto-fixable with `ruff check --select F401 --fix`. Key files:

| File | Unused imports |
|------|---------------|
| `backend/core/equalizer/client_proxy.py` | `asyncio`, `CLIENT_REQUEST_TIMEOUT` |
| `backend/core/equalizer/multiroom_service.py` | `Any`, `Dict`, `CompressorSettings`, `LoudnessSettings` |
| `backend/core/multiroom/crossover.py` | `List` |
| `backend/core/multiroom/equalizer_router.py` | `Optional` |
| `backend/core/multiroom/routes.py` | `HTTPException` |
| `backend/core/multiroom/routing.py` | `SystemdServiceManager` |
| `backend/core/multiroom/snapcast.py` | `Optional` |
| `backend/core/updates/version.py` | `Optional`, `Path` |
| `backend/core/volume/service.py` | `Any` |
| `backend/core/volume/state.py` | `time` |
| `backend/hardware/screen.py` | `os` |
| `backend/hardware/service.py` | `Path`, `SCREENS` |
| `backend/shared/mpv_audio_source.py` | `Dict`, `Any` |
| `backend/sources/podcast/taddy_api.py` | `asyncio` |

**Tag:** `[REMOVE]` -- run `ruff check backend/ --select F401 --fix`

### 1.4 Python -- Unused Test Code (121 items)

98 unused imports + 22 unused variables across test files. Not individually listed -- all auto-fixable.

**Tag:** `[REMOVE]` -- run `ruff check backend/tests/ milo-client/app/tests/ --select F401,F841 --fix`

### 1.5 JavaScript/Vue -- Unused Components

| Component | File | Tag |
|-----------|------|-----|
| `SignalDots` | `frontend/src/components/settings/categories/wifi/SignalDots.vue` | `[REMOVE]` -- superseded by `WifiSignal` |

### 1.6 JavaScript/Vue -- Unused Exports

| Symbol | File | Line | Tag |
|--------|------|------|-----|
| `cleanDeviceName` | `frontend/src/utils/deviceName.js` | 10 | `[REMOVE]` |

### 1.7 JavaScript/Vue -- Unused Functions & Variables in Source

#### Stores

| File | Line | Symbol | Tag |
|------|------|--------|-----|
| `equalizerStore.js` | 256 | `fetchZoneCrossover` | `[REMOVE]` |
| `equalizerStore.js` | 353 | `isMacAddress` | `[REMOVE]` |
| `equalizerStore.js` | 584 | `presetsData` | `[REMOVE]` |
| `equalizerStore.js` | 1000 | `sourceClient` | `[REMOVE]` |
| `multiroomStore.js` | 173 | `volume_db`, `mute` (destructured, unused) | `[REMOVE]` |
| `podcastStore.js` | 5 | `logger` import | `[REMOVE]` |
| `unifiedAudioStore.js` | 3 | `computed` import | `[REMOVE]` |

#### Schemas (entire file may be dead)

14 schema definitions in `frontend/src/schemas/api.js` are assigned but never used:

`WebSocketMessageSchema`, `VolumeEventDataSchema`, `SourceEventDataSchema`, `ApiResponseSchema`, `HealthResponseSchema`, `EqualizerFilterSchema`, `EqualizerStatusSchema`, `EqualizerZoneResponseSchema`, `EqualizerCompressorSchema`, `EqualizerLoudnessSchema`, `EqualizerPresetsResponseSchema`, `MultiroomStateSchema`, `RadioStationSchema`, `PodcastEpisodeSchema`

**Tag:** `[REMOVED]` -- confirmed dead, only SystemStateSchema/VolumeStateSchema/validateSchema are used in production; test updated to match

#### Components (unused variables/imports)

| File | Symbol | Tag |
|------|--------|-----|
| `ManageStation.vue:268` | `removeImage` function | `[REMOVE]` |
| `EpisodeCard.vue:135` | `progressPercent` | `[REMOVE]` |
| `ScreenStep.vue:19` | `computed` import | `[REMOVE]` |
| `LevelMeters.vue:26` | `ref` import | `[REMOVE]` |

#### Composables

| File | Symbol | Tag |
|------|--------|-----|
| `useDockDrag.js:1` | `onMounted` import | `[REMOVE]` |
| `useSettingsAPI.js:2` | `ref` import | `[REMOVE]` |
| `useVolumeHold.js:1` | `ref` import | `[REMOVE]` |
| `useAnimatedHeight.js:159` | `bcrH` variable | `[REMOVE]` |
| `useViewTransition.js:252` | `enteringBCR` variable | `[REMOVE]` |

### 1.8 Dead WebSocket Wiring

#### Frontend handler for event never emitted by backend

| Category | Type | Location | Tag |
|----------|------|----------|-----|
| `source` | `metadata` | `App.vue:417` | `[REMOVE]` -- backend sends metadata via `state_changed`, never as separate `metadata` event |

#### Backend events with no frontend handler (dropped silently)

| Category | Type | Backend source | Tag |
|----------|------|---------------|-----|
| `wifi` | `connect_failed` | `core/wifi/service.py:203,215,225` | `[RESOLVED]` -- no frontend handler, but emission is correct and harmless; frontend could add handler |
| `wifi` | `connected` | `core/wifi/service.py:236` | `[RESOLVED]` -- same |
| `wifi` | `network_forgotten` | `core/wifi/service.py:249` | `[RESOLVED]` -- same |
| `equalizer` | `crossover_changed` | `core/equalizer/service.py:752` | `[RESOLVED]` -- no frontend handler; local crossover endpoints flagged for removal in §1.9 |

### 1.9 Dead API Endpoints (no frontend caller, no server-to-server use)

| # | Method | Path | File:Line | Tag |
|---|--------|------|-----------|-----|
| 1 | GET | `/api/radio/favorites` | `radio/routes.py:207` | `[REMOVED]` -- frontend uses `/stations?favorites_only=true` |
| 2 | PUT | `/api/radio/custom/update` | `radio/routes.py:460` | `[REMOVE]` |
| 3 | PUT | `/api/radio/custom/{station_id}/image` | `radio/routes.py:536` | `[REMOVE]` |
| 4 | POST | `/api/radio/custom/from-favorite` | `radio/routes.py:628` | `[REMOVE]` |
| 5 | GET | `/api/equalizer/levels` | `equalizer.py:121` | `[REMOVE]` -- frontend uses zone endpoint |
| 6 | GET | `/api/equalizer/client/{client_id}/type` | `equalizer.py:533` | `[REMOVE]` |
| 7 | GET | `/api/equalizer/client-types` | `equalizer.py:558` | `[REMOVE]` |
| 8 | GET | `/api/equalizer/crossover` | `equalizer.py:602` | `[REMOVE]` -- local crossover via zone endpoints |
| 9 | PUT | `/api/equalizer/crossover` | `equalizer.py:612` | `[REMOVE]` |
| 10 | GET | `/api/equalizer/client/{hostname}/enabled` | `equalizer.py:717` | `[REMOVE]` -- state pushed, not polled |
| 11 | GET | `/api/equalizer/client/{hostname}/volume` | `equalizer.py:738` | `[REMOVE]` -- volume via `/api/volume/` |
| 12 | PUT | `/api/equalizer/client/{hostname}/volume` | `equalizer.py:747` | `[REMOVE]` |
| 13 | PUT | `/api/equalizer/client/{hostname}/mute` | `equalizer.py:770` | `[REMOVE]` -- mute via `/api/volume/client/mac/` |
| 14 | GET | `/api/equalizer/client/{hostname}/saved-settings` | `equalizer.py:792` | `[REMOVE]` -- internal sync only |
| 15 | PATCH | `/api/volume/client/{client_id}` | `volume.py:228` | `[REMOVE]` -- superseded by MAC-based endpoint |
| 16 | PATCH | `/api/volume/client/{client_id}/mute` | `volume.py:260` | `[REMOVE]` -- superseded by MAC-based endpoint |
| 17 | GET | `/api/volume/client/{client_id}` | `volume.py:288` | `[REMOVE]` -- volume state via WS |
| 18 | GET | `/api/volume/zone/{zone_id}` | `volume.py:160` | `[REMOVE]` -- zone volume via WS |
| 19 | GET | `/api/volume/settings` | `volume.py:384` | `[REMOVE]` -- loaded via `/api/settings/bulk` |
| 20 | PATCH | `/api/volume/settings` | `volume.py:399` | `[REMOVE]` -- managed via `/api/settings/` |
| 21 | PATCH | `/api/multiroom/pending-clients/{mac_id}` | `multiroom.py:558` | `[REMOVE]` -- not wired in UI |

**Note:** 15 individual settings GET endpoints (e.g. `/api/settings/volume-limits`) are also uncalled but intentionally kept as part of the REST API surface -- the frontend uses `/api/settings/bulk` instead. Not flagged for removal.

---

## 2. Dead Assets & Config

### 2.1 Unused i18n Keys (17 keys)

#### `setup.mode.*` -- entire sub-namespace dead (6 keys) `[REMOVE]`

| Key | Notes |
|-----|-------|
| `setup.mode.title` | Mode selection step was removed |
| `setup.mode.server` | |
| `setup.mode.serverDescription` | |
| `setup.mode.client` | |
| `setup.mode.clientDescription` | |
| `setup.summary.mode` | References removed mode selection |

#### `podcasts.*` -- replaced by Intl API (4 keys) `[REMOVE]`

| Key | Notes |
|-----|-------|
| `podcasts.daysAgo` | `EpisodeCard.vue` uses `Intl.DateTimeFormat` |
| `podcasts.weeksAgo` | |
| `podcasts.monthsAgo` | |
| `podcasts.yearsAgo` | |

#### `audioSources.cdSource.*` -- labels never rendered (4 keys) `[REMOVE]`

| Key | Notes |
|-----|-------|
| `audioSources.cdSource.insertDisc` | Icon-only buttons used |
| `audioSources.cdSource.noDisc` | |
| `audioSources.cdSource.eject` | |
| `audioSources.cdSource.tracklist` | |

#### `network.*` -- unreferenced (2 keys) `[REMOVE]`

| Key | Notes |
|-----|-------|
| `network.connectedTo` | Not referenced in any template |
| `network.saved` | Not referenced in any template |

#### `status.audioReceivedFrom` -- `[RESOLVED]`

Confirmed alive: used in `useScreensaver.js` composable (line 180) for the Mac source screensaver title.

### 2.2 Dead Rootfs Config Files

| File | Notes | Tag |
|------|-------|-----|
| `rootfs/home/milo/.bash_profile` | Dead kiosk launch path -- `getty@tty1` is masked, `milo-kiosk.service` launches Cage directly | `[REMOVE]` |
| `rootfs/home/milo/.config/milo-cage-start.sh` | Stale Chromium flags diverging from active service; both files deployed on every update | `[REMOVE]` |

### 2.3 Dead Shell Script Code

| File | Line | Issue | Tag |
|------|------|-------|-----|
| `install.sh` | 20,94,287,1013 | `REBOOT_REQUIRED` flag set but never read -- script always reboots unconditionally at L1185 | `[REMOVE]` |
| `install.sh` | 166 | Commented-out `git clone` -- dead code | `[REMOVE]` |
| `pi-gen/stage-milo/01-install-audio/01-run.sh` | 65-66 | `systemctl stop` in build chroot does nothing (systemd not running) | `[REMOVE]` |
| `pi-gen/stage-milo/03-configure/00-run.sh` | 213 | `systemctl stop lightdm` in build chroot -- dead code | `[REMOVE]` |
| `pi-gen/stage-milo/02-install-milo/01-run.sh` | 4-5 | `MILO_APP_DIR`, `MILO_DATA_DIR` unused (paths hardcoded in heredocs) | `[REMOVE]` |
| `pi-gen/stage-milo/03-configure/00-run.sh` | 5 | `MILO_APP_DIR` unused | `[REMOVE]` |

### 2.4 Systemd Stale Config

| File | Line | Issue | Tag |
|------|------|-------|-----|
| `milo-backend.service` | 14 | `#Environment="GITHUB_TOKEN=ADD_TOKEN_HERE"` -- dead placeholder | `[REMOVE]` |

### 2.5 Duplicated Systemd Disable Commands

`pi-gen/stage-milo/03-configure/01-run.sh` L36-40 duplicates `systemctl disable` commands already done in `01-install-audio/` scripts.

**Tag:** `[REMOVE]`

---

## 3. Duplicated Logic

### 3.1 Volume Client Endpoints -- Two Addressing Schemes

Volume control has two parallel sets of client endpoints:

| By `client_id` (legacy) | By MAC address (current) |
|--------------------------|--------------------------|
| `PATCH /api/volume/client/{client_id}` | `PATCH /api/volume/client/mac/{mac_url}` |
| `PATCH /api/volume/client/{client_id}/mute` | `PATCH /api/volume/client/mac/{mac_url}/mute` |
| `GET /api/volume/client/{client_id}` | (via WS state) |

Frontend only uses the MAC-based endpoints.

**Candidates for centralization:** Remove client_id-based endpoints entirely.
**Complexity:** Low -- delete 3 route handlers + tests.
**Tag:** `[REMOVE]`

### 3.2 Equalizer Volume/Mute -- Duplicates Volume API

Three equalizer endpoints duplicate volume API functionality:

| Equalizer endpoint | Volume equivalent |
|--------------------|-------------------|
| `GET /api/equalizer/client/{hostname}/volume` | `GET /api/volume/client/{client_id}` |
| `PUT /api/equalizer/client/{hostname}/volume` | `PATCH /api/volume/client/mac/{mac_url}` |
| `PUT /api/equalizer/client/{hostname}/mute` | `PATCH /api/volume/client/mac/{mac_url}/mute` |

**Complexity:** Low -- delete 3 route handlers.
**Tag:** `[REMOVE]`

### 3.3 Local vs Zone Crossover Endpoints

| Local endpoint | Zone equivalent |
|----------------|-----------------|
| `GET /api/equalizer/crossover` | `GET /api/equalizer/links/{zone_id}/crossover` |
| `PUT /api/equalizer/crossover` | `PUT /api/equalizer/links/{zone_id}/crossover` |

Frontend only uses the zone-scoped endpoints.

**Complexity:** Low.
**Tag:** `[REMOVE]`

### 3.4 `install.sh` / `install-client.sh` -- Shared Patterns

Both scripts have near-identical:
- Log functions (`log_info`, `log_warn`, `log_error`, `log_step`)
- Journald `sed` configuration blocks
- Temp directory patterns with no cleanup traps

**Candidates for centralization:** Extract shared functions into a `install/common.sh` sourced by both.
**Complexity:** Medium -- both scripts are standalone install-time artifacts.
**Tag:** `[REFACTOR]`

### 3.5 `milo-apply-hardware` / `milo-client-apply-hardware` -- Shared Overlay Logic

Both scripts have nearly identical `sed` overlay logic for `/boot/firmware/config.txt`, with the same bug (destroys existing comma-separated options on dtoverlay lines).

**Candidates for centralization:** Extract shared config.txt manipulation into a library script.
**Complexity:** Medium.
**Tag:** `[REFACTOR]`

---

## 4. Inconsistent Patterns

### 4.1 i18n Genre Keys -- Mixed Naming

Both camelCase and snake_case versions exist for the same genres:

| camelCase (used in `HomeView.vue`) | snake_case (used in `SearchView.vue`) |
|------------------------------------|---------------------------------------|
| `podcasts.genres.trueCrime` | `podcasts.genres.true_crime` |
| `podcasts.genres.health` | `podcasts.genres.health_and_fitness` |

Both spellings are referenced -- not dead, but inconsistent.

**Tag:** `[REFACTOR]` -- pick one convention, update both views

### 4.2 Systemd `Requires=sound.target` -- Missing on 2 Audio Services

| Service | Has `Requires=sound.target` |
|---------|:---------------------------:|
| milo-radio | Yes |
| milo-podcast | Yes |
| milo-cd | Yes |
| milo-camilladsp | Yes |
| **milo-mac** | **No** |
| **milo-airplay** | **No** |

**Tag:** `[REFACTOR]` -- add `Requires=sound.target` to both

### 4.3 Systemd `Group=` -- Missing on `milo-bluealsa-aplay`

All other audio-playing services specify `Group=audio` or `Group=milo`. `milo-bluealsa-aplay` only has `User=milo` with no `Group=`.

**Tag:** `[REFACTOR]` -- add `Group=audio`

### 4.4 ESLint `vue/multi-word-component-names` -- 7 UI Components

Single-word component names violate Vue style guide:

`Button.vue`, `Dock.vue`, `Dropdown.vue`, `Logo.vue`, `Modal.vue`, `Radio.vue`, `Toggle.vue`

All in `frontend/src/components/ui/`. Not a bug -- cosmetic convention issue.

**Tag:** `[STYLE DECISION]` -- not dead code; renaming is a convention choice with breaking import changes

### 4.5 ESLint `vue/no-side-effects-in-computed-properties` -- 3 Errors

| File | Line | Tag |
|------|------|-----|
| `AudioPlayerFull.vue` | 128 | `[REFACTOR]` |
| `MainView.vue` | 87 | `[REFACTOR]` |
| `MainView.vue` | 95 | `[REFACTOR]` |

Computed properties with side effects can cause infinite render loops. Should be refactored to use `watch` or methods.

### 4.6 Unused `props`/`emit` Assignments in Vue Components

11 components assign `props` or `emit` to a variable but never reference it. Harmless but noisy:

`ItemSelector.vue`, `GenreView.vue`, `PodcastDetails.vue`, `QueueView.vue`, `SearchView.vue`, `SubscriptionsView.vue`, `FavoritesView.vue`, `SettingsModal.vue`, `LanguageStep.vue`, `NavigationHeader.vue`, `Toggle.vue`, `ToggleSection.vue`

**Tag:** `[REMOVE]` -- remove unused assignments

---

## 5. Shell Script Issues (Non-Dead-Code)

### 5.1 High Severity

| File | Line | Issue | Tag |
|------|------|-------|-----|
| `install.sh` | 1084 | Third-party `install.sh` from Waveshare `Brightness.zip` runs without checksum verification | `[SECURITY]` -- supply chain risk, not dead code |
| `pi-gen/.../03-configure/00-run.sh` | 192-199 | External `wget` from Waveshare with no checksum -- supply chain risk | `[SECURITY]` -- not dead code |
| `milo-client-deploy-update` | 48 | Pipe subshell prevents `set -e` from catching copy failures | `[REFACTOR]` |
| `milo-client-deploy-update` | 37 | Avahi override filename mismatch (`override.conf` vs `milo-override.conf`) | `[REFACTOR]` |
| `milo-client/install-client.sh` | 411-418 | Variables in `bash -c` strings break if path contains spaces | `[REFACTOR]` |

### 5.2 Medium Severity (Recurring Patterns)

| Pattern | Files affected | Tag |
|---------|---------------|-----|
| SC2155: `local var=$(cmd)` masks return codes | `install.sh` (11 sites), `install/airplay.sh` (2), `install-client.sh` (3) | `[REFACTOR]` |
| No `trap` for temp directory cleanup on interrupt | `install.sh` (6 temp dirs), `install/airplay.sh` (2) | `[REFACTOR]` |
| `sed` patterns for journald too strict -- silently fail on format variations | `install.sh:431-432`, `install-client.sh:228-229` | `[REFACTOR]` |
| `rootfs/usr/local/bin/milo-wait-ready.sh:98` | "All services are ready!" logged even when waits fail | `[REFACTOR]` |
| `rootfs/usr/local/bin/milo-apply-hardware:116-123` | `sed` destroys existing comma-separated overlay options | `[REFACTOR]` |

---

## 6. Whitespace & Style (Auto-Fixable)

### Python (107 issues)

98 blank-line-with-whitespace (W293), 7 no-newline-at-EOF (W292), 2 trailing-whitespace (W291). Top files: `settings.py` (44), `routing.py` (23), `systemd.py` (20).

```bash
ruff check backend/ --select W291,W292,W293 --fix
```

**Tag:** `[REMOVE]`

### Python Line Length (~2427 hits)

Current limit is 88 chars. Consider adding `pyproject.toml` with `line-length = 120`.

**Tag:** `[STYLE DECISION]` -- team decision on line length policy

---

## 7. Action Plan Summary

| Tag | Count | Description |
|-----|-------|-------------|
| `[REMOVE]` | ~73 | Safe to delete -- no references anywhere |
| `[REMOVED]` | ~16 | Verified dead and removed |
| `[RESOLVED]` | ~8 | Verified alive (dynamic access, composable usage, harmless emission) |
| `[REFACTOR]` | ~20 | Needs rewrite, consolidation, or pattern fix |
| `[STYLE DECISION]` | ~2 | Team decision on naming/formatting conventions |
| `[SECURITY]` | ~2 | Supply chain risk, not dead code |

### Recommended Execution Order

1. **Auto-fix pass** (5 min): Run ruff to remove unused imports + whitespace in both source and tests
2. **Dead component/export removal** (10 min): `SignalDots.vue`, `cleanDeviceName`, dead i18n keys, dead WS handler
3. **Dead API endpoints** (30 min): Remove 21 unused routes + their backing methods
4. **Dead Python methods** (30 min): Remove ~41 unused methods/properties after grep verification
5. **Dead shell code** (15 min): `REBOOT_REQUIRED`, dead systemctl calls in chroot, stale rootfs configs
6. **Systemd consistency** (5 min): Add `Requires=sound.target` and `Group=` where missing
7. **Vue side-effect computed** (15 min): Refactor 3 computed properties to watches
8. **Genre key consolidation** (10 min): Pick one naming convention for i18n genre keys
9. **Shell script hardening** (45 min): Cleanup traps, SC2155 fixes, sed robustness
