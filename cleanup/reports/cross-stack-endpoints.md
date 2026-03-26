# Backend API Endpoints vs Frontend Usage — Cross-Reference Report

**Date:** 2026-03-26

---

## How to Read This Report

- **Full path** = resolved URL (prefix + route path)
- **Frontend caller(s)** = file and rough call site
- **NO FRONTEND CALLER** = flagged with a warning marker

---

## 1. `backend/sources/bluetooth/routes.py`

Router prefix: `/bluetooth` — mounted under `/api` → effective prefix `/api/bluetooth`

| # | Method | Full Path | Line | Frontend Caller(s) |
|---|--------|-----------|------|--------------------|
| 1 | GET | `/api/bluetooth/status` | 42 | **NO FRONTEND CALLER** |
| 2 | POST | `/api/bluetooth/disconnect` | 57 | `frontend/src/stores/unifiedAudioStore.js` (line 91) |

---

## 2. `backend/sources/airplay/routes.py`

Router prefix: `/airplay` — mounted under `/api` → effective prefix `/api/airplay`

| # | Method | Full Path | Line | Frontend Caller(s) |
|---|--------|-----------|------|--------------------|
| 3 | GET | `/api/airplay/status` | 37 | **NO FRONTEND CALLER** |
| 4 | GET | `/api/airplay/artwork` | 55 | **NO FRONTEND CALLER** |
| 5 | POST | `/api/airplay/restart` | 70 | **NO FRONTEND CALLER** |

---

## 3. `backend/sources/cd/routes.py`

Router prefix: `/cd` — mounted under `/api` → effective prefix `/api/cd`

| # | Method | Full Path | Line | Frontend Caller(s) |
|---|--------|-----------|------|--------------------|
| 6 | GET | `/api/cd/status` | 46 | **NO FRONTEND CALLER** |
| 7 | GET | `/api/cd/drive-status` | 62 | `frontend/src/stores/cdStore.js` (line 44); `DockSettings.vue` |
| 8 | POST | `/api/cd/play` | 80 | `frontend/src/stores/cdStore.js` (line 32) |
| 9 | POST | `/api/cd/pause` | 90 | **NO FRONTEND CALLER** |
| 10 | POST | `/api/cd/resume` | 96 | **NO FRONTEND CALLER** |
| 11 | POST | `/api/cd/next` | 102 | **NO FRONTEND CALLER** |
| 12 | POST | `/api/cd/prev` | 108 | **NO FRONTEND CALLER** |
| 13 | POST | `/api/cd/seek` | 114 | **NO FRONTEND CALLER** |
| 14 | POST | `/api/cd/stop` | 124 | **NO FRONTEND CALLER** |
| 15 | POST | `/api/cd/eject` | 130 | `frontend/src/stores/cdStore.js` (line 38) |
| 16 | GET | `/api/cd/tracks` | 139 | **NO FRONTEND CALLER** |
| 17 | GET | `/api/cd/disc-info` | 145 | **NO FRONTEND CALLER** |
| 18 | GET | `/api/cd/cover/{disc_id}` | 167 | **NO FRONTEND CALLER** (URL likely in `<img>` tag via WS metadata) |

---

## 4. `backend/sources/podcast/routes.py`

Router prefix: `/podcast` — mounted under `/api` → effective prefix `/api/podcast`

| # | Method | Full Path | Line | Frontend Caller(s) |
|---|--------|-----------|------|--------------------|
| 19 | GET | `/api/podcast/status` | 52 | **NO FRONTEND CALLER** |
| 20 | GET | `/api/podcast/discover/top-charts` | 74 | `HomeView.vue` (lines 189, 206) |
| 21 | GET | `/api/podcast/discover/by-genre` | 120 | `GenreView.vue` (line 61) |
| 22 | GET | `/api/podcast/lookup/itunes/{itunes_id}` | 160 | `PodcastSource.vue` (line 243) |
| 23 | GET | `/api/podcast/search` | 187 | `SearchView.vue` (lines 234, 250, 261) |
| 24 | GET | `/api/podcast/series/{uuid}` | 256 | `PodcastDetails.vue` (lines 91, 136) |
| 25 | GET | `/api/podcast/episode/{uuid}` | 294 | `EpisodeDetails.vue` (line 65) |
| 26 | POST | `/api/podcast/play` | 321 | `podcastStore.js` (line 103) |
| 27 | POST | `/api/podcast/pause` | 340 | `podcastStore.js` (line 122) |
| 28 | POST | `/api/podcast/resume` | 348 | `podcastStore.js` (line 128) |
| 29 | POST | `/api/podcast/seek` | 356 | `podcastStore.js` (line 134) |
| 30 | POST | `/api/podcast/stop` | 365 | `podcastStore.js` (line 141) |
| 31 | POST | `/api/podcast/speed` | 373 | `podcastStore.js` (line 148) |
| 32 | GET | `/api/podcast/subscriptions` | 384 | `podcastStore.js` (lines 337, 366) |
| 33 | POST | `/api/podcast/subscriptions` | 397 | `PodcastDetails.vue` (line 104) |
| 34 | DELETE | `/api/podcast/subscriptions/{uuid}` | 416 | `PodcastDetails.vue` (line 123); `SubscriptionsView.vue` (line 68) |
| 35 | GET | `/api/podcast/subscriptions/latest-episodes` | 430 | `podcastStore.js` (line 375) |
| 36 | GET | `/api/podcast/queue` | 465 | `QueueView.vue` (line 63) |
| 37 | POST | `/api/podcast/queue/{episode_uuid}/complete` | 478 | `QueueView.vue` (line 70) |
| 38 | GET | `/api/podcast/settings` | 494 | `podcastStore.js` (line 159) |
| 39 | POST | `/api/podcast/settings` | 507 | `podcastStore.js` (line 169) |

---

## 5. `backend/sources/radio/routes.py`

Router prefix: `/radio` — mounted under `/api` → effective prefix `/api/radio`

| # | Method | Full Path | Line | Frontend Caller(s) |
|---|--------|-----------|------|--------------------|
| 40 | GET | `/api/radio/status` | 54 | **NO FRONTEND CALLER** |
| 41 | POST | `/api/radio/play` | 81 | `radioStore.js` (line 343) |
| 42 | POST | `/api/radio/stop` | 102 | `radioStore.js` (line 353) |
| 43 | GET | `/api/radio/stations` | 115 | `radioStore.js` (lines 199, 220, 274) |
| 44 | GET | `/api/radio/countries` | 190 | `RadioSource.vue` (line 238); `ManageStation.vue` (line 166) |
| 45 | GET | `/api/radio/favorites` | 207 | **NO FRONTEND CALLER** (frontend uses `/stations?favorites_only=true` instead) |
| 46 | POST | `/api/radio/favorites/add` | 222 | `radioStore.js` (line 366) |
| 47 | DELETE | `/api/radio/favorites/{station_id}` | 242 | `radioStore.js` (line 376) |
| 48 | POST | `/api/radio/favorites/modify-metadata` | 261 | `ManageStation.vue` (line 348) |
| 49 | POST | `/api/radio/favorites/restore-metadata` | 318 | `SettingsModal.vue` (line 387) |
| 50 | GET | `/api/radio/custom` | 346 | `radioStore.js` (line 474) |
| 51 | POST | `/api/radio/custom/add` | 363 | `radioStore.js` (line 409) |
| 52 | DELETE | `/api/radio/custom/{station_id}` | 431 | `radioStore.js` (line 431) |
| 53 | PUT | `/api/radio/custom/update` | 460 | **NO FRONTEND CALLER** |
| 54 | PUT | `/api/radio/custom/{station_id}/image` | 536 | **NO FRONTEND CALLER** |
| 55 | DELETE | `/api/radio/custom/{station_id}/image` | 589 | `radioStore.js` (line 447) |
| 56 | POST | `/api/radio/custom/from-favorite` | 628 | **NO FRONTEND CALLER** |
| 57 | GET | `/api/radio/images/{filename}` | 690 | Dynamic URL in `RadioSource.vue`, `StationCard.vue`, `useScreensaver.js` |
| 58 | GET | `/api/radio/favicon` | 736 | `RadioSource.vue`, `StationCard.vue`, `useScreensaver.js` |

---

## 6. `backend/core/multiroom/routes.py`

Router prefix: `/api/routing/snapcast`

| # | Method | Full Path | Line | Frontend Caller(s) |
|---|--------|-----------|------|--------------------|
| 59 | GET | `/api/routing/snapcast/server-config` | 69 | `snapcastStore.js` (line 142) |
| 60 | POST | `/api/routing/snapcast/server/config` | 93 | `snapcastStore.js` (line 217) |

---

## 7. `backend/api/system.py`

Router prefix: `/api/system`

| # | Method | Full Path | Line | Frontend Caller(s) |
|---|--------|-----------|------|--------------------|
| 61 | POST | `/api/system/restart` | 28 | `NetworkSettings.vue` (line 220); `SettingsModal.vue` (line 497) |
| 62 | POST | `/api/system/shutdown` | 35 | `SettingsModal.vue` (line 512) |

---

## 8. `backend/api/equalizer.py`

Router prefix: `/api/equalizer`

| # | Method | Full Path | Line | Frontend Caller(s) |
|---|--------|-----------|------|--------------------|
| 63 | GET | `/api/equalizer/enabled` | 75 | `equalizerStore.js` (line 265) |
| 64 | PUT | `/api/equalizer/enabled` | 87 | `equalizerStore.js` (line 272) |
| 65 | GET | `/api/equalizer/status` | 108 | `equalizerStore.js` (line 200) |
| 66 | GET | `/api/equalizer/levels` | 121 | **NO FRONTEND CALLER** (frontend always uses zone endpoint) |
| 67 | GET | `/api/equalizer/levels/zone/{client_ids}` | 130 | `LevelMeters.vue` (line 87) |
| 68 | GET | `/api/equalizer/filters` | 176 | `equalizerStore.js` (line 214) |
| 69 | PUT | `/api/equalizer/filter/{filter_id}` | 185 | `equalizerStore.js` (line 240) |
| 70 | POST | `/api/equalizer/reset` | 222 | `equalizerStore.js` (lines 248, 776) |
| 71 | GET | `/api/equalizer/presets` | 235 | `equalizerStore.js` (line 222) |
| 72 | PUT | `/api/equalizer/preset/{preset_id}` | 250 | `equalizerStore.js` (line 808) |
| 73 | POST | `/api/equalizer/save-custom` | 261 | `equalizerStore.js` (line 833) |
| 74 | POST | `/api/equalizer/zone/{zone_id}/save-custom` | 272 | `equalizerStore.js` (line 829) |
| 75 | POST | `/api/equalizer/client/{mac_id}/save-custom` | 287 | `equalizerStore.js` (line 831) |
| 76 | GET | `/api/equalizer/zone/{zone_id}` | 302 | `equalizerStore.js` (line 207) |
| 77 | POST | `/api/equalizer/zone/{zone_id}/preset` | 314 | `equalizerStore.js` (line 796) |
| 78 | PATCH | `/api/equalizer/zone/{zone_id}/filter/{filter_id}` | 333 | `equalizerStore.js` (line 236) |
| 79 | PATCH | `/api/equalizer/zone/{zone_id}/compressor` | 361 | `equalizerStore.js` (line 896) |
| 80 | PATCH | `/api/equalizer/zone/{zone_id}/loudness` | 385 | `equalizerStore.js` (line 919) |
| 81 | PATCH | `/api/equalizer/zone/{zone_id}/enabled` | 406 | `equalizerStore.js` (line 1391) |
| 82 | POST | `/api/equalizer/client/{mac_id}/preset` | 432 | `equalizerStore.js` (line 508) |
| 83 | PUT | `/api/equalizer/mute` | 452 | `equalizerStore.js` (line 939) |
| 84 | GET | `/api/equalizer/compressor` | 475 | `equalizerStore.js` (line 905) |
| 85 | PUT | `/api/equalizer/compressor` | 484 | `equalizerStore.js` (line 905) |
| 86 | GET | `/api/equalizer/loudness` | 505 | `equalizerStore.js` (line 928) |
| 87 | PUT | `/api/equalizer/loudness` | 514 | `equalizerStore.js` (line 928) |
| 88 | GET | `/api/equalizer/client/{client_id}/type` | 533 | **NO FRONTEND CALLER** |
| 89 | PUT | `/api/equalizer/client/{client_id}/crossover-frequency` | 544 | `equalizerStore.js` (line 1109) |
| 90 | GET | `/api/equalizer/client-types` | 558 | **NO FRONTEND CALLER** |
| 91 | GET | `/api/equalizer/links/{zone_id}/crossover` | 567 | `equalizerStore.js` (line 258) |
| 92 | GET | `/api/equalizer/links/{zone_id}/auto-crossover` | 576 | `equalizerStore.js` (line 1144) |
| 93 | PUT | `/api/equalizer/links/{zone_id}/crossover` | 585 | `equalizerStore.js` (line 1157) |
| 94 | POST | `/api/equalizer/links/{zone_id}/crossover/apply` | 594 | `equalizerStore.js` (line 1178) |
| 95 | GET | `/api/equalizer/crossover` | 602 | **NO FRONTEND CALLER** |
| 96 | PUT | `/api/equalizer/crossover` | 612 | **NO FRONTEND CALLER** |
| 97 | GET | `/api/equalizer/client/{hostname}/status` | 630 | `equalizerStore.js` (line 200, for remote clients) |
| 98 | GET | `/api/equalizer/client/{hostname}/filters` | 646 | `equalizerStore.js` (line 214, for remote clients) |
| 99 | PUT | `/api/equalizer/client/{hostname}/filter/{filter_id}` | 651 | `equalizerStore.js` (line 240, for remote clients) |
| 100 | POST | `/api/equalizer/client/{hostname}/reset` | 669 | `equalizerStore.js` (line 776, for remote clients) |
| 101 | GET | `/api/equalizer/client/{hostname}/compressor` | 679 | `equalizerStore.js` (line 905, for remote clients) |
| 102 | PUT | `/api/equalizer/client/{hostname}/compressor` | 684 | `equalizerStore.js` (line 905, for remote clients) |
| 103 | GET | `/api/equalizer/client/{hostname}/loudness` | 698 | `equalizerStore.js` (line 928, for remote clients) |
| 104 | PUT | `/api/equalizer/client/{hostname}/loudness` | 703 | `equalizerStore.js` (line 928, for remote clients) |
| 105 | GET | `/api/equalizer/client/{hostname}/enabled` | 717 | **NO FRONTEND CALLER** |
| 106 | PUT | `/api/equalizer/client/{hostname}/enabled` | 722 | `equalizerStore.js` (line 510) |
| 107 | GET | `/api/equalizer/client/{hostname}/volume` | 738 | **NO FRONTEND CALLER** |
| 108 | PUT | `/api/equalizer/client/{hostname}/volume` | 747 | **NO FRONTEND CALLER** |
| 109 | PUT | `/api/equalizer/client/{hostname}/mute` | 770 | **NO FRONTEND CALLER** |
| 110 | GET | `/api/equalizer/client/{hostname}/saved-settings` | 792 | **NO FRONTEND CALLER** |
| 111 | POST | `/api/equalizer/client/{hostname}/restore` | 800 | `equalizerStore.js` (line 990) |

---

## 9. `backend/api/programs.py`

Router prefix: `/api/programs`

| # | Method | Full Path | Line | Frontend Caller(s) |
|---|--------|-----------|------|--------------------|
| 112 | GET | `/api/programs` | 97 | `UpdateManager.vue` (line 342) |
| 113 | GET | `/api/programs/satellites` | 117 | `UpdateManager.vue` (line 405) |
| 114 | GET | `/api/programs/satellites/{mac_id}` | 159 | **NO FRONTEND CALLER** |
| 115 | POST | `/api/programs/satellites/{mac_id}/update` | 175 | `UpdateManager.vue` (line 432) |
| 116 | POST | `/api/programs/satellites/{mac_id}/update-app` | 203 | `UpdateManager.vue` (line 460) |
| 117 | GET | `/api/programs/satellites/{mac_id}/update-status` | 232 | **NO FRONTEND CALLER** |
| 118 | GET | `/api/programs/{program_key}` | 252 | **NO FRONTEND CALLER** |
| 119 | GET | `/api/programs/{program_key}/installed` | 268 | `InfoSettings.vue` (line 109) |
| 120 | POST | `/api/programs/{program_key}/update` | 284 | `UpdateManager.vue` (line 386) |

---

## 10. `backend/api/routing.py`

Router prefix: `/api/routing`

| # | Method | Full Path | Line | Frontend Caller(s) |
|---|--------|-----------|------|--------------------|
| 121 | PUT | `/api/routing/multiroom` | 19 | `unifiedAudioStore.js` (line 76) |

---

## 11. `backend/api/wifi.py`

Router prefix: `/api/wifi`

| # | Method | Full Path | Line | Frontend Caller(s) |
|---|--------|-----------|------|--------------------|
| 122 | GET | `/api/wifi/networks` | 16 | `useWifi.js` (line 128) |
| 123 | GET | `/api/wifi/status` | 23 | `useWifi.js` (lines 29, 88) |
| 124 | POST | `/api/wifi/connect` | 30 | `useWifi.js` (line 142) |
| 125 | POST | `/api/wifi/save` | 37 | `useWifi.js` (line 160) |
| 126 | DELETE | `/api/wifi/saved/{ssid}` | 44 | `useWifi.js` (line 179) |
| 127 | GET | `/api/wifi/saved` | 51 | `useWifi.js` (line 118) |
| 128 | PUT | `/api/wifi/radio` | 58 | `useWifi.js` (line 214) |
| 129 | GET | `/api/wifi/hotspot/status` | 66 | **NO FRONTEND CALLER** |
| 130 | GET | `/api/wifi/country` | 71 | `useWifi.js` (line 98) |
| 131 | PUT | `/api/wifi/country` | 78 | `useWifi.js` (line 108) |

---

## 12. `backend/api/setup.py`

Router prefix: `/api/setup`

| # | Method | Full Path | Line | Frontend Caller(s) |
|---|--------|-----------|------|--------------------|
| 132 | POST | `/api/setup/complete` | 29 | `SetupWizard.vue` (line 181) |

---

## 13. `backend/api/health.py`

Router prefix: `/api`

| # | Method | Full Path | Line | Frontend Caller(s) |
|---|--------|-----------|------|--------------------|
| 133 | GET | `/api/health` | 14 | **NO FRONTEND CALLER** (monitoring/ops only) |
| 134 | GET | `/api/ping` | 90 | `NetworkSettings.vue`; `HardwareSettings.vue`; `SetupWizard.vue` |
| 135 | GET | `/api/initial-state` | 95 | `App.vue` (line 168) |

---

## 14. `backend/api/audio.py`

Router prefix: `/api/audio`

| # | Method | Full Path | Line | Frontend Caller(s) |
|---|--------|-----------|------|--------------------|
| 136 | GET | `/api/audio/state` | 16 | `websocket.js` (line 189) |
| 137 | POST | `/api/audio/source/{source_name}` | 22 | `unifiedAudioStore.js` (line 50) |
| 138 | POST | `/api/audio/control/{source_name}` | 32 | `unifiedAudioStore.js` (line 62) |

---

## 15. `backend/api/multiroom.py`

Router prefix: `/api/multiroom`

| # | Method | Full Path | Line | Frontend Caller(s) |
|---|--------|-----------|------|--------------------|
| 139 | GET | `/api/multiroom/state` | 86 | `multiroomStore.js` (line 184) |
| 140 | GET | `/api/multiroom/clients/{mac_id}` | 117 | **NO FRONTEND CALLER** |
| 141 | PATCH | `/api/multiroom/clients/{mac_id}` | 137 | `multiroomStore.js` (line 537) |
| 142 | DELETE | `/api/multiroom/clients/{mac_id}` | 173 | `multiroomStore.js` (line 550) |
| 143 | GET | `/api/multiroom/clients/{mac_id}/hardware` | 203 | `multiroomStore.js` (line 564) |
| 144 | PUT | `/api/multiroom/clients/{mac_id}/audio` | 222 | `multiroomStore.js` (line 579) |
| 145 | GET | `/api/multiroom/zones` | 251 | **NO FRONTEND CALLER** (state fetched via `/api/multiroom/state`) |
| 146 | GET | `/api/multiroom/zones/{zone_id}` | 268 | **NO FRONTEND CALLER** |
| 147 | POST | `/api/multiroom/zones` | 288 | `multiroomStore.js` (line 440) |
| 148 | PATCH | `/api/multiroom/zones/{zone_id}` | 332 | `multiroomStore.js` (line 469) |
| 149 | DELETE | `/api/multiroom/zones/{zone_id}` | 366 | `multiroomStore.js` (line 456) |
| 150 | POST | `/api/multiroom/zones/{zone_id}/clients` | 399 | `multiroomStore.js` (line 487) |
| 151 | DELETE | `/api/multiroom/zones/{zone_id}/clients/{mac_id}` | 447 | `multiroomStore.js` (line 506) |
| 152 | POST | `/api/multiroom/register-client` | 504 | **NO FRONTEND CALLER** (called by milo-client firmware) |
| 153 | GET | `/api/multiroom/pending-clients` | 552 | `multiroomStore.js` (line 594) |
| 154 | PATCH | `/api/multiroom/pending-clients/{mac_id}` | 558 | **NO FRONTEND CALLER** |
| 155 | POST | `/api/multiroom/pending-clients/{mac_id}/configure` | 572 | `multiroomStore.js` (line 610) |

---

## 16. `backend/api/volume.py`

Router prefix: `/api/volume`

| # | Method | Full Path | Line | Frontend Caller(s) |
|---|--------|-----------|------|--------------------|
| 156 | GET | `/api/volume/state` | 23 | `websocket.js` (line 190) |
| 157 | POST | `/api/volume/adjust` | 32 | `unifiedAudioStore.js` (line 143) |
| 158 | PATCH | `/api/volume/zone/{zone_id}` | 109 | `equalizerStore.js` (line 403) |
| 159 | GET | `/api/volume/zone/{zone_id}` | 160 | **NO FRONTEND CALLER** |
| 160 | PATCH | `/api/volume/client/{client_id}` | 228 | **NO FRONTEND CALLER** (superseded by MAC-based endpoint) |
| 161 | PATCH | `/api/volume/client/{client_id}/mute` | 260 | **NO FRONTEND CALLER** (superseded by MAC-based endpoint) |
| 162 | GET | `/api/volume/client/{client_id}` | 288 | **NO FRONTEND CALLER** |
| 163 | PATCH | `/api/volume/client/mac/{mac_url}` | 318 | `equalizerStore.js` (line 377) |
| 164 | PATCH | `/api/volume/client/mac/{mac_url}/mute` | 351 | `equalizerStore.js` (lines 455, 468) |
| 165 | GET | `/api/volume/settings` | 384 | **NO FRONTEND CALLER** (loaded via `/api/settings/bulk`) |
| 166 | PATCH | `/api/volume/settings` | 399 | **NO FRONTEND CALLER** |
| 167 | PATCH | `/api/volume/volume-control` | 435 | `HardwareSettings.vue` (line 178) |

---

## 17. `backend/api/settings.py`

Router prefix: `/api/settings`

| # | Method | Full Path | Line | Frontend Caller(s) |
|---|--------|-----------|------|--------------------|
| 168 | GET | `/api/settings/bulk` | 106 | `settingsStore.js` (line 140) |
| 169 | GET | `/api/settings/language` | 163 | `i18n.js` (line 98) |
| 170 | PUT | `/api/settings/language` | 167 | `i18n.js` (line 115); `LanguageSettings.vue` |
| 171 | GET | `/api/settings/volume-limits` | 178 | **NO FRONTEND CALLER** (loaded via bulk) |
| 172 | PUT | `/api/settings/volume-limits` | 189 | `VolumeSettings.vue` |
| 173 | GET | `/api/settings/volume-startup` | 207 | **NO FRONTEND CALLER** (loaded via bulk) |
| 174 | PUT | `/api/settings/volume-startup` | 218 | `VolumeSettings.vue` |
| 175 | GET | `/api/settings/volume-steps` | 236 | **NO FRONTEND CALLER** (loaded via bulk) |
| 176 | PUT | `/api/settings/volume-steps` | 244 | `VolumeSettings.vue` |
| 177 | GET | `/api/settings/rotary-steps` | 256 | **NO FRONTEND CALLER** (loaded via bulk) |
| 178 | PUT | `/api/settings/rotary-steps` | 264 | `VolumeSettings.vue` |
| 179 | GET | `/api/settings/bt-remote-steps` | 275 | **NO FRONTEND CALLER** (loaded via bulk) |
| 180 | PUT | `/api/settings/bt-remote-steps` | 284 | `VolumeSettings.vue` |
| 181 | GET | `/api/settings/dock-apps` | 296 | **NO FRONTEND CALLER** (loaded via bulk) |
| 182 | PUT | `/api/settings/dock-apps` | 306 | `DockSettings.vue` |
| 183 | GET | `/api/settings/spotify-disconnect` | 515 | **NO FRONTEND CALLER** (loaded via bulk) |
| 184 | PUT | `/api/settings/spotify-disconnect` | 523 | `SpotifySettings.vue` |
| 185 | GET | `/api/settings/podcast-credentials` | 547 | **NO FRONTEND CALLER** (loaded via bulk) |
| 186 | PUT | `/api/settings/podcast-credentials` | 558 | `PodcastSettings.vue` |
| 187 | POST | `/api/settings/podcast-credentials/validate` | 593 | `PodcastSettings.vue` (line 166) |
| 188 | GET | `/api/settings/podcast-credentials/status` | 643 | `settingsStore.js` (lines 141, 300) |
| 189 | GET | `/api/settings/screen-timeout` | 675 | **NO FRONTEND CALLER** (loaded via bulk) |
| 190 | PUT | `/api/settings/screen-timeout` | 690 | `ScreenSettings.vue` |
| 191 | GET | `/api/settings/screen-brightness` | 702 | **NO FRONTEND CALLER** (loaded via bulk) |
| 192 | PUT | `/api/settings/screen-brightness` | 710 | `ScreenSettings.vue` |
| 193 | POST | `/api/settings/screen-brightness/apply` | 721 | `ScreenSettings.vue` (line 156) |
| 194 | GET | `/api/settings/screen-screensaver` | 752 | **NO FRONTEND CALLER** (loaded via bulk) |
| 195 | PUT | `/api/settings/screen-screensaver` | 763 | `ScreenSettings.vue` |
| 196 | GET | `/api/settings/screen-ui-scale` | 788 | **NO FRONTEND CALLER** (loaded via bulk) |
| 197 | PUT | `/api/settings/screen-ui-scale` | 796 | `ScreenSettings.vue` |
| 198 | POST | `/api/settings/screen-activity` | 806 | `useScreenActivity.js`; `App.vue` |
| 199 | GET | `/api/settings/screen-debug` | 816 | **NO FRONTEND CALLER** (debug endpoint) |
| 200 | GET | `/api/settings/system-temperature` | 837 | `InfoSettings.vue` (line 124) |
| 201 | GET | `/api/settings/network-info` | 933 | `InfoSettings.vue` (line 144) |
| 202 | GET | `/api/settings/system-resources` | 973 | `InfoSettings.vue` (line 164) |
| 203 | GET | `/api/settings/hardware-info` | 1023 | `useHardwareConfig.js` (line 50) |
| 204 | GET | `/api/settings/hardware-config` | 1048 | `useHardwareConfig.js` (line 98) |
| 205 | PUT | `/api/settings/hardware-config` | 1077 | `HardwareSettings.vue` (line 211) |
| 206 | GET | `/api/settings/mac-roc` | 1140 | **NO FRONTEND CALLER** (loaded via bulk) |
| 207 | PUT | `/api/settings/mac-roc` | 1153 | `MacSettings.vue` (line 140) |
| 208 | GET | `/api/settings/radio-settings` | 1206 | **NO FRONTEND CALLER** (loaded via bulk) |
| 209 | PUT | `/api/settings/radio-settings` | 1216 | `RadioSettings.vue` |
| 210 | GET | `/api/settings/inactivity-timeout` | 1242 | **NO FRONTEND CALLER** (loaded via bulk) |
| 211 | PUT | `/api/settings/inactivity-timeout` | 1251 | `DockSettings.vue` |

---

## 18. `backend/hardware/bt_remote_routes.py`

Router prefix: `/api/bt-remote`

| # | Method | Full Path | Line | Frontend Caller(s) |
|---|--------|-----------|------|--------------------|
| 212 | GET | `/api/bt-remote/status` | 21 | `settingsStore.js` (line 328) |
| 213 | GET | `/api/bt-remote/battery` | 29 | `settingsStore.js` (line 353) |
| 214 | POST | `/api/bt-remote/discover` | 48 | `settingsStore.js` (line 362) |
| 215 | PATCH | `/api/bt-remote/config` | 54 | `settingsStore.js` (line 343) |

---

## 19. `backend/api/errors.py`

Router prefix: `/api/errors`

| # | Method | Full Path | Line | Frontend Caller(s) |
|---|--------|-----------|------|--------------------|
| 216 | POST | `/api/errors` | 18 | `main.js` (line 19) |

---

## Summary: Endpoints with ZERO Frontend Callers

**61 endpoints** have no direct frontend callers. Grouped by reason:

### Truly Unused / Legacy (candidates for removal)

| # | Method | Path | File:Line | Notes |
|---|--------|------|-----------|-------|
| 1 | GET | `/api/radio/favorites` | `radio/routes.py:207` | Frontend uses `/stations?favorites_only=true` instead |
| 2 | PUT | `/api/radio/custom/update` | `radio/routes.py:460` | No frontend wiring |
| 3 | PUT | `/api/radio/custom/{station_id}/image` | `radio/routes.py:536` | No frontend wiring |
| 4 | POST | `/api/radio/custom/from-favorite` | `radio/routes.py:628` | No frontend wiring |
| 5 | GET | `/api/equalizer/levels` | `equalizer.py:121` | Frontend uses zone endpoint instead |
| 6 | GET | `/api/equalizer/client/{client_id}/type` | `equalizer.py:533` | No frontend caller |
| 7 | GET | `/api/equalizer/client-types` | `equalizer.py:558` | No frontend caller |
| 8 | GET | `/api/equalizer/crossover` | `equalizer.py:602` | Local crossover managed via zone endpoints |
| 9 | PUT | `/api/equalizer/crossover` | `equalizer.py:612` | Local crossover managed via zone endpoints |
| 10 | GET | `/api/equalizer/client/{hostname}/enabled` | `equalizer.py:717` | State pushed, not polled |
| 11 | GET | `/api/equalizer/client/{hostname}/volume` | `equalizer.py:738` | Volume via `/api/volume/` |
| 12 | PUT | `/api/equalizer/client/{hostname}/volume` | `equalizer.py:747` | Volume via `/api/volume/` |
| 13 | PUT | `/api/equalizer/client/{hostname}/mute` | `equalizer.py:770` | Mute via `/api/volume/client/mac/` |
| 14 | GET | `/api/equalizer/client/{hostname}/saved-settings` | `equalizer.py:792` | Internal sync only |
| 15 | PATCH | `/api/volume/client/{client_id}` | `volume.py:228` | Superseded by MAC-based endpoint |
| 16 | PATCH | `/api/volume/client/{client_id}/mute` | `volume.py:260` | Superseded by MAC-based endpoint |
| 17 | GET | `/api/volume/client/{client_id}` | `volume.py:288` | Volume state from WS |
| 18 | GET | `/api/volume/zone/{zone_id}` | `volume.py:160` | Zone volume state from WS |
| 19 | GET | `/api/volume/settings` | `volume.py:384` | Loaded via `/api/settings/bulk` |
| 20 | PATCH | `/api/volume/settings` | `volume.py:399` | Managed via `/api/settings/` endpoints |
| 21 | PATCH | `/api/multiroom/pending-clients/{mac_id}` | `multiroom.py:558` | Not wired in UI |

### Source-Specific Status Endpoints (state comes via WebSocket)

| # | Method | Path | File:Line |
|---|--------|------|-----------|
| 22 | GET | `/api/bluetooth/status` | `bluetooth/routes.py:42` |
| 23 | GET | `/api/airplay/status` | `airplay/routes.py:37` |
| 24 | GET | `/api/cd/status` | `cd/routes.py:46` |
| 25 | GET | `/api/podcast/status` | `podcast/routes.py:52` |
| 26 | GET | `/api/radio/status` | `radio/routes.py:54` |

### CD Playback Controls (routed via generic `audio/control` instead)

| # | Method | Path | File:Line |
|---|--------|------|-----------|
| 27 | POST | `/api/cd/pause` | `cd/routes.py:90` |
| 28 | POST | `/api/cd/resume` | `cd/routes.py:96` |
| 29 | POST | `/api/cd/next` | `cd/routes.py:102` |
| 30 | POST | `/api/cd/prev` | `cd/routes.py:108` |
| 31 | POST | `/api/cd/seek` | `cd/routes.py:114` |
| 32 | POST | `/api/cd/stop` | `cd/routes.py:124` |
| 33 | GET | `/api/cd/tracks` | `cd/routes.py:139` |
| 34 | GET | `/api/cd/disc-info` | `cd/routes.py:145` |
| 35 | GET | `/api/cd/cover/{disc_id}` | `cd/routes.py:167` |

### AirPlay-Specific (no remote control, state via WS)

| # | Method | Path | File:Line |
|---|--------|------|-----------|
| 36 | GET | `/api/airplay/artwork` | `airplay/routes.py:55` |
| 37 | POST | `/api/airplay/restart` | `airplay/routes.py:70` |

### Individual Settings GETs (all loaded via `/api/settings/bulk`)

| # | Method | Path | File:Line |
|---|--------|------|-----------|
| 38 | GET | `/api/settings/volume-limits` | `settings.py:178` |
| 39 | GET | `/api/settings/volume-startup` | `settings.py:207` |
| 40 | GET | `/api/settings/volume-steps` | `settings.py:236` |
| 41 | GET | `/api/settings/rotary-steps` | `settings.py:256` |
| 42 | GET | `/api/settings/bt-remote-steps` | `settings.py:275` |
| 43 | GET | `/api/settings/dock-apps` | `settings.py:296` |
| 44 | GET | `/api/settings/spotify-disconnect` | `settings.py:515` |
| 45 | GET | `/api/settings/podcast-credentials` | `settings.py:547` |
| 46 | GET | `/api/settings/screen-timeout` | `settings.py:675` |
| 47 | GET | `/api/settings/screen-brightness` | `settings.py:702` |
| 48 | GET | `/api/settings/screen-screensaver` | `settings.py:752` |
| 49 | GET | `/api/settings/screen-ui-scale` | `settings.py:788` |
| 50 | GET | `/api/settings/mac-roc` | `settings.py:1140` |
| 51 | GET | `/api/settings/radio-settings` | `settings.py:1206` |
| 52 | GET | `/api/settings/inactivity-timeout` | `settings.py:1242` |

### Server-to-Server / Ops / Debug

| # | Method | Path | File:Line | Notes |
|---|--------|------|-----------|-------|
| 53 | GET | `/api/health` | `health.py:14` | Monitoring endpoint |
| 54 | GET | `/api/settings/screen-debug` | `settings.py:816` | Debug endpoint |
| 55 | POST | `/api/multiroom/register-client` | `multiroom.py:504` | Called by milo-client firmware |
| 56 | GET | `/api/multiroom/clients/{mac_id}` | `multiroom.py:117` | Full state via `/api/multiroom/state` |
| 57 | GET | `/api/multiroom/zones` | `multiroom.py:251` | Full state via `/api/multiroom/state` |
| 58 | GET | `/api/multiroom/zones/{zone_id}` | `multiroom.py:268` | Full state via `/api/multiroom/state` |
| 59 | GET | `/api/programs/satellites/{mac_id}` | `programs.py:159` | Frontend fetches list |
| 60 | GET | `/api/programs/satellites/{mac_id}/update-status` | `programs.py:232` | Progress via WS |
| 61 | GET | `/api/programs/{program_key}` | `programs.py:252` | Frontend only uses `/installed` |
| 62 | GET | `/api/wifi/hotspot/status` | `wifi.py:66` | Hotspot status from initial_state |
