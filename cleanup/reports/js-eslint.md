# ESLint Report — Frontend

Generated: 2026-03-26
Tool: eslint v10.1.0 + eslint-plugin-vue
Rules: `no-unused-vars: warn`, `no-unreachable: warn` (+ vue/essential preset)
Command: `npx eslint -c eslint-scan.config.mjs --no-config-lookup "src/"`

## Summary

**62 problems (11 errors, 51 warnings)**

---

## Errors (11)

### vue/no-side-effects-in-computed-properties (3)

```
src/components/audio/AudioPlayerFull.vue:128:5
src/views/MainView.vue:87:5
src/views/MainView.vue:95:5
```

### vue/multi-word-component-names (7)

```
src/components/ui/Button.vue:1:1
src/components/ui/Dock.vue:1:1
src/components/ui/Dropdown.vue:1:1
src/components/ui/Logo.vue:1:1
src/components/ui/Modal.vue:1:1
src/components/ui/Radio.vue:1:1
src/components/ui/Toggle.vue:1:1
```

### vue/no-unused-vars (1)

```
src/components/ui/Dropdown.vue:17:32  — 'index' is defined but never used
```

---

## Warnings — no-unused-vars (51)

### Components

```
src/components/equalizer/ItemSelector.vue:36:7          — 'props' is assigned a value but never used
src/components/equalizer/LevelMeters.vue:26:10          — 'ref' is defined but never used
src/components/equalizer/LevelMeters.vue:99:12          — 'error' is defined but never used
src/components/multiroom/MultiroomItem.vue:430:26       — '_' is assigned a value but never used
src/components/podcasts/EpisodeCard.vue:135:7           — 'progressPercent' is assigned a value but never used
src/components/podcasts/GenreView.vue:50:7              — 'emit' is assigned a value but never used
src/components/podcasts/PodcastDetails.vue:74:7         — 'emit' is assigned a value but never used
src/components/podcasts/QueueView.vue:38:7              — 'emit' is assigned a value but never used
src/components/podcasts/QueueView.vue:39:7              — 'podcastStore' is assigned a value but never used
src/components/podcasts/SearchView.vue:92:7             — 'emit' is assigned a value but never used
src/components/podcasts/SubscriptionsView.vue:48:7      — 'emit' is assigned a value but never used
src/components/radio/FavoritesView.vue:37:7             — 'props' is assigned a value but never used
src/components/settings/SettingsModal.vue:267:7         — 'emit' is assigned a value but never used
src/components/settings/categories/UpdateManager.vue:488:31  — 'message' is assigned a value but never used
src/components/settings/categories/UpdateManager.vue:488:40  — 'error' is assigned a value but never used
src/components/settings/categories/UpdateManager.vue:488:47  — 'old_version' is assigned a value but never used
src/components/settings/categories/UpdateManager.vue:488:60  — 'new_version' is assigned a value but never used
src/components/settings/categories/radio/ManageStation.vue:268:10 — 'removeImage' is defined but never used
src/components/setup/LanguageStep.vue:47:7              — 'props' is assigned a value but never used
src/components/setup/LanguageStep.vue:56:9              — 't' is assigned a value but never used
src/components/setup/ScreenStep.vue:19:10               — 'computed' is defined but never used
src/components/ui/NavigationHeader.vue:52:7             — 'props' is assigned a value but never used
src/components/ui/Toggle.vue:14:7                       — 'props' is assigned a value but never used
src/components/ui/ToggleSection.vue:25:7                — 'props' is assigned a value but never used
```

### Composables

```
src/composables/useAnimatedHeight.js:159:17   — 'bcrH' is assigned a value but never used
src/composables/useDockDrag.js:1:15           — 'onMounted' is defined but never used
src/composables/useSettingsAPI.js:2:10        — 'ref' is defined but never used
src/composables/useViewTransition.js:252:15   — 'enteringBCR' is assigned a value but never used
src/composables/useVolumeHold.js:1:10         — 'ref' is defined but never used
```

### Stores

```
src/stores/equalizerStore.js:256:18     — 'fetchZoneCrossover' is defined but never used
src/stores/equalizerStore.js:353:12     — 'isMacAddress' is defined but never used
src/stores/equalizerStore.js:584:39     — 'presetsData' is assigned a value but never used
src/stores/equalizerStore.js:1000:41    — 'sourceClient' is assigned a value but never used
src/stores/multiroomStore.js:173:13     — 'volume_db' is assigned a value but never used
src/stores/multiroomStore.js:173:24     — 'mute' is assigned a value but never used
src/stores/podcastStore.js:5:10         — 'logger' is defined but never used
src/stores/unifiedAudioStore.js:3:15    — 'computed' is defined but never used
```

### Schemas

```
src/schemas/api.js:86:7    — 'WebSocketMessageSchema' is assigned a value but never used
src/schemas/api.js:93:7    — 'VolumeEventDataSchema' is assigned a value but never used
src/schemas/api.js:99:7    — 'SourceEventDataSchema' is assigned a value but never used
src/schemas/api.js:106:7   — 'ApiResponseSchema' is assigned a value but never used
src/schemas/api.js:112:7   — 'HealthResponseSchema' is assigned a value but never used
src/schemas/api.js:122:7   — 'EqualizerFilterSchema' is assigned a value but never used
src/schemas/api.js:131:7   — 'EqualizerStatusSchema' is assigned a value but never used
src/schemas/api.js:142:7   — 'EqualizerZoneResponseSchema' is assigned a value but never used
src/schemas/api.js:156:7   — 'EqualizerCompressorSchema' is assigned a value but never used
src/schemas/api.js:165:7   — 'EqualizerLoudnessSchema' is assigned a value but never used
src/schemas/api.js:177:7   — 'EqualizerPresetsResponseSchema' is assigned a value but never used
src/schemas/api.js:202:7   — 'MultiroomStateSchema' is assigned a value but never used
src/schemas/api.js:217:7   — 'RadioStationSchema' is assigned a value but never used
src/schemas/api.js:241:7   — 'PodcastEpisodeSchema' is assigned a value but never used
```
