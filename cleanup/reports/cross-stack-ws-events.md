# WebSocket Event Cross-Reference Report

**Date:** 2026-03-26

---

## Methodology

- **Backend sources:** All `.py` files under `backend/` searched for `broadcast_event(` calls
- **Frontend sources:** `App.vue`, `UpdateManager.vue`, `RadioSettings.vue`, `websocket.js`, and all store/composable files searched for `ws.on(` registrations

---

## Section 1: All `broadcast_event()` Calls in Backend

| # | File | Line | Category | Type |
|---|------|------|----------|------|
| 1 | `backend/hardware/screen.py` | 191 | `settings` | `screen_sleep_changed` |
| 2 | `backend/hardware/bt_remote.py` | 192 | `settings` | `bt_remote_config_changed` |
| 3 | `backend/hardware/bt_remote.py` | 222 | `settings` | `bt_remote_status_changed` |
| 4 | `backend/core/log_handler.py` | 47 | `system` | `backend_error` |
| 5 | `backend/core/wifi/service.py` | 203 | `wifi` | `connect_failed` |
| 6 | `backend/core/wifi/service.py` | 215 | `wifi` | `connect_failed` |
| 7 | `backend/core/wifi/service.py` | 225 | `wifi` | `connect_failed` |
| 8 | `backend/core/wifi/service.py` | 236 | `wifi` | `connected` |
| 9 | `backend/core/wifi/service.py` | 249 | `wifi` | `network_forgotten` |
| 10 | `backend/core/state.py` | 137 | `system` | `transition_start` |
| 11 | `backend/core/state.py` | 165 | `system` | `transition_complete` |
| 12 | `backend/core/state.py` | 184 | `system` | `error` |
| 13 | `backend/core/state.py` | 200 | `system` | `error` |
| 14 | `backend/core/state.py` | 240 | `source` | `state_changed` |
| 15 | `backend/core/state.py` | 260 | `system` | `state_changed` |
| 16 | `backend/core/state.py` | 281 | `system` | `state_changed` |
| 17 | `backend/core/state.py` | 343 | `system` | `state_changed` |
| 18 | `backend/core/equalizer/service.py` | 247 | `equalizer` | `state_changed` |
| 19 | `backend/core/equalizer/service.py` | 295 | `equalizer` | `state_changed` |
| 20 | `backend/core/equalizer/service.py` | 466 | `equalizer` | `filter_changed` |
| 21 | `backend/core/equalizer/service.py` | 496 | `equalizer` | `filters_reset` |
| 22 | `backend/core/equalizer/service.py` | 636 | `equalizer` | `compressor_changed` |
| 23 | `backend/core/equalizer/service.py` | 721 | `equalizer` | `loudness_changed` |
| 24 | `backend/core/equalizer/service.py` | 752 | `equalizer` | `crossover_changed` |
| 25 | `backend/core/equalizer/service.py` | 839 | `equalizer` | `preset_loaded` |
| 26 | `backend/core/equalizer/multiroom_service.py` | 662 | `multiroom` | `equalizer_changed` |
| 27 | `backend/core/equalizer/multiroom_service.py` | 955 | `equalizer` | `zone_enabled_changed` |
| 28 | `backend/core/equalizer/multiroom_service.py` | 987 | `multiroom` | `equalizer_changed` |
| 29 | `backend/core/audio_source.py` | 606 | `source` | `position_update` |
| 30 | `backend/core/audio_source.py` | 633 | `source` | `state_changed` |
| 31 | `backend/core/audio_source.py` | 659 | `source` | `error_cleared` |
| 32 | `backend/sources/radio/data.py` | 371 | `source` | `favorite_added` |
| 33 | `backend/sources/radio/data.py` | 392 | `source` | `favorite_removed` |
| 34 | `backend/sources/radio/data.py` | 675 | `source` | `favorite_modified` |
| 35 | `backend/core/volume/service.py` | 376 | `settings` | `volume_startup_changed` |
| 36 | `backend/core/volume/service.py` | 891 | `volume` | `volume_changed` |
| 37 | `backend/core/multiroom/client_registry.py` | 1185 | `multiroom` | `client_state_changed` |
| 38 | `backend/core/multiroom/client_registry.py` | 1185 | `multiroom` | `zone_changed` |
| 39 | `backend/core/multiroom/client_registry.py` | 1185 | `multiroom` | `equalizer_changed` |
| 40 | `backend/core/multiroom/routes.py` | 29 | `system` | `state_changed` |
| 41 | `backend/core/multiroom/crossover.py` | 234 | `multiroom` | `crossover_changed` |
| 42 | `backend/core/multiroom/crossover.py` | 260 | `multiroom` | `crossover_changed` |
| 43 | `backend/core/multiroom/crossover.py` | 369 | `multiroom` | `crossover_changed` |
| 44 | `backend/core/multiroom/crossover.py` | 562 | `multiroom` | `zone_changed` |
| 45 | `backend/core/multiroom/crossover.py` | 680 | `multiroom` | `crossover_changed` |
| 46 | `backend/core/multiroom/routing.py` | 446 | `routing` | `multiroom_error` |
| 47 | `backend/core/multiroom/routing.py` | 458 | `routing` | `multiroom_enabling` / `multiroom_disabling` |
| 48 | `backend/core/multiroom/routing.py` | 495 | `routing` | `multiroom_ready` |
| 49 | `backend/core/multiroom/routing.py` | 530 | `equalizer` | `enabled_changed` |
| 50 | `backend/core/multiroom/pending_clients.py` | 194 | `multiroom` | `pending_client_changed` |
| 51 | `backend/sources/cd/source.py` | 207 | `system` | `cd_drive_status` |
| 52 | `backend/sources/cd/source.py` | 252 | `system` | `cd_drive_status` |
| 53 | `backend/sources/cd/source.py` | 280 | `system` | `cd_drive_status` |
| 54 | `backend/sources/cd/source.py` | 351 | `system` | `cd_drive_status` |
| 55 | `backend/api/settings.py` | 80 | `settings` | `language_changed` |
| 56 | `backend/api/settings.py` | 80 | `settings` | `volume_limits_changed` |
| 57 | `backend/api/settings.py` | 80 | `settings` | `volume_steps_changed` |
| 58 | `backend/api/settings.py` | 80 | `settings` | `rotary_steps_changed` |
| 59 | `backend/api/settings.py` | 80 | `settings` | `bt_remote_steps_changed` |
| 60 | `backend/api/settings.py` | 80 | `settings` | `spotify_disconnect_changed` |
| 61 | `backend/api/settings.py` | 80 | `settings` | `podcast_credentials_changed` |
| 62 | `backend/api/settings.py` | 80 | `settings` | `screen_timeout_changed` |
| 63 | `backend/api/settings.py` | 80 | `settings` | `screen_brightness_changed` |
| 64 | `backend/api/settings.py` | 80 | `settings` | `screen_screensaver_changed` |
| 65 | `backend/api/settings.py` | 80 | `settings` | `screen_ui_scale_changed` |
| 66 | `backend/api/settings.py` | 80 | `settings` | `radio_settings_changed` |
| 67 | `backend/api/settings.py` | 80 | `settings` | `inactivity_timeout_changed` |
| 68 | `backend/api/settings.py` | 330/484 | `settings` | `dock_apps_changed` |
| 69 | `backend/api/settings.py` | 1187 | `settings` | `mac_roc_changed` |
| 70 | `backend/api/programs.py` | 57-90 | `programs` | `program_update_progress` |
| 71 | `backend/api/programs.py` | 57-90 | `programs` | `program_update_complete` |
| 72 | `backend/api/programs.py` | 57-90 | `programs` | `satellite_update_progress` |
| 73 | `backend/api/programs.py` | 57-90 | `programs` | `satellite_update_complete` |
| 74 | `backend/api/programs.py` | 57-90 | `programs` | `satellite_app_update_progress` |
| 75 | `backend/api/programs.py` | 57-90 | `programs` | `satellite_app_update_complete` |

> **Note:** `system.ping` and `system.initial_state` are generated internally by the WebSocket connection manager, not via `broadcast_event()`.

---

## Section 2: Frontend Event Handler Registrations

### `App.vue` (lines 381-568)

| Category | Type | Handler |
|----------|------|---------|
| `system` | `initial_state` | `processInitialState(event)` |
| `volume` | `volume_changed` | `unifiedStore.handleVolumeEvent(event)` |
| `system` | `state_changed` | `unifiedStore.updateState(event)` |
| `system` | `transition_start` | `unifiedStore.updateState(event)` |
| `system` | `transition_complete` | `unifiedStore.updateState(event)` |
| `source` | `state_changed` | `unifiedStore.updateState` + `podcastStore` + `cdStore` |
| `source` | `position_update` | `unifiedStore.updatePosition` + `podcastStore` |
| `source` | `error_cleared` | Clears `currentError` ref |
| `system` | `error` | Error notification banner |
| `system` | `backend_error` | Error notification banner |
| `source` | `metadata` | `unifiedStore.updateState` + `podcastStore` + `cdStore` |
| `system` | `cd_drive_status` | `cdStore.handleSystemEvent(event)` |
| `settings` | `language_changed` | `i18n` + `settingsStore` |
| `settings` | `dock_apps_changed` | `settingsStore.updateDockApps` |
| `settings` | `screen_sleep_changed` | `settingsStore.updateScreenSleeping` |
| `routing` | `multiroom_enabling` | `multiroomStore.handleRoutingEvent` |
| `routing` | `multiroom_disabling` | `multiroomStore.handleRoutingEvent` |
| `routing` | `multiroom_ready` | `multiroomStore.handleRoutingEvent` |
| `routing` | `multiroom_error` | `multiroomStore.handleRoutingEvent` |
| `multiroom` | `client_state_changed` | `multiroomStore.handleMultiroomEvent` |
| `multiroom` | `zone_changed` | `multiroomStore.handleMultiroomEvent` |
| `multiroom` | `pending_client_changed` | `multiroomStore.handleMultiroomEvent` |
| `multiroom` | `equalizer_changed` | `equalizerStore.handleEqualizerChanged` |
| `multiroom` | `crossover_changed` | `equalizerStore.handleZoneCrossoverChanged` |
| `source` | `favorite_added` | `radioStore.handleFavoriteEvent` |
| `source` | `favorite_removed` | `radioStore.handleFavoriteEvent` |
| `source` | `favorite_modified` | `radioStore.handleMetadataModified` |
| `settings` | `volume_limits_changed` | `settingsStore.updateVolumeLimits` |
| `settings` | `volume_startup_changed` | `settingsStore.updateVolumeStartup` |
| `settings` | `volume_steps_changed` | `unifiedStore.updateMobileStep` |
| `settings` | `rotary_steps_changed` | `settingsStore.updateVolumeSteps` |
| `settings` | `bt_remote_steps_changed` | `settingsStore.updateVolumeSteps` |
| `settings` | `spotify_disconnect_changed` | `settingsStore.updateSpotifyDisconnect` |
| `settings` | `screen_timeout_changed` | `settingsStore.updateScreenTimeout` |
| `settings` | `screen_brightness_changed` | `settingsStore.updateScreenBrightness` |
| `settings` | `screen_screensaver_changed` | `settingsStore.updateScreenScreensaver` |
| `settings` | `screen_ui_scale_changed` | `settingsStore.updateScreenUiScale` |
| `settings` | `radio_settings_changed` | `settingsStore.updateRadioSettings` |
| `settings` | `mac_roc_changed` | `settingsStore.updateMacRocSettings` |
| `settings` | `inactivity_timeout_changed` | `settingsStore.updateInactivityTimeout` |
| `settings` | `bt_remote_config_changed` | `settingsStore.updateBtRemoteConfig` |
| `settings` | `bt_remote_status_changed` | `settingsStore.updateBtRemoteStatus` |
| `settings` | `podcast_credentials_changed` | `settingsStore.updatePodcastCredentials` |
| `equalizer` | `filter_changed` | `equalizerStore.handleFilterChanged` |
| `equalizer` | `filters_reset` | `equalizerStore.handleFiltersReset` |
| `equalizer` | `state_changed` | `equalizerStore.handleStateChanged` |
| `equalizer` | `preset_loaded` | `equalizerStore.handlePresetLoaded` |
| `equalizer` | `compressor_changed` | `equalizerStore.handleCompressorChanged` |
| `equalizer` | `loudness_changed` | `equalizerStore.handleLoudnessChanged` |
| `equalizer` | `enabled_changed` | `equalizerStore.handleEnabledChanged` |
| `equalizer` | `zone_enabled_changed` | `equalizerStore.handleZoneEnabledChanged` |

### `UpdateManager.vue` (lines 540-544)

| Category | Type | Handler |
|----------|------|---------|
| `programs` | `program_update_progress` | Updates `localUpdateStates` |
| `programs` | `program_update_complete` | Clears state, calls `loadLocalPrograms()` |
| `programs` | `satellite_update_progress` | Updates `satelliteUpdateStates` |
| `programs` | `satellite_update_complete` | Clears state, calls `loadSatellites()` |
| `programs` | `satellite_app_update_progress` | Updates `satelliteAppUpdateStates` |
| `programs` | `satellite_app_update_complete` | Clears state, calls `loadSatellites()` |

### `RadioSettings.vue` (line 107)

| Category | Type | Handler |
|----------|------|---------|
| `source` | `favorite_modified` | `radioStore.loadRadioSettingsData()` |

### Internal `websocket.js`

| Category | Type | Handler |
|----------|------|---------|
| `system` | `ping` | Updates `lastPingTime` (does not propagate) |

---

## Section 3: FLAGGED — Backend Events with NO Frontend Handler

| # | Category | Type | Backend Source | Notes |
|---|----------|------|---------------|-------|
| **1** | `wifi` | `connect_failed` | `core/wifi/service.py:203,215,225` | Frontend uses HTTP polling (`useWifi.js`), no WS handler |
| **2** | `wifi` | `connected` | `core/wifi/service.py:236` | Same — successful connection event is dropped |
| **3** | `wifi` | `network_forgotten` | `core/wifi/service.py:249` | Same — forget network confirmation dropped |
| **4** | `equalizer` | `crossover_changed` | `core/equalizer/service.py:752` | Frontend handles `multiroom.crossover_changed` but NOT `equalizer.crossover_changed` |

---

## Section 4: FLAGGED — Frontend Handlers for Events NEVER Emitted by Backend

| # | Category | Type | Frontend Location | Notes |
|---|----------|------|--------------------|-------|
| **1** | `source` | `metadata` | `App.vue:417` | No `broadcast_event("source", "metadata", ...)` exists in backend. Metadata travels via `source.state_changed`. This handler is dead code. |

---

## Summary

| Status | Count |
|--------|-------|
| Backend events with matching frontend handler | **51** unique category.type pairs |
| Backend events with **no** frontend handler (dropped) | **4** (`wifi.*` x3, `equalizer.crossover_changed`) |
| Frontend handlers for events **never emitted** (dead) | **1** (`source.metadata`) |
