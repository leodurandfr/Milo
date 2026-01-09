# Component Inventory - Frontend

> Generated: 2026-01-09 | Total Components: 77

## Overview

The Milo frontend uses Vue 3 Composition API with a component-based architecture organized by domain.

---

## Component Categories

| Category | Count | Description |
|----------|-------|-------------|
| Audio | 4 | Core audio player components |
| Spotify | 3 | Spotify Connect UI |
| Radio | 5 | Internet radio UI |
| Podcasts | 15 | Podcast browsing & playback |
| Multiroom | 4 | Multi-room audio controls |
| Settings | 18 | Configuration panels |
| DSP | 9 | Audio processing controls |
| UI | 17 | Reusable UI primitives |
| Navigation | 2 | App navigation |

---

## Audio Components (`components/audio/`)

| Component | Description |
|-----------|-------------|
| `AudioPlayer.vue` | Main audio player wrapper |
| `AudioSourceView.vue` | Source-specific view container |
| `AudioSourceLayout.vue` | Common layout for audio sources |
| `AudioSourceStatus.vue` | Status display for audio sources |

---

## Spotify Components (`components/spotify/`)

| Component | Description |
|-----------|-------------|
| `SpotifySource.vue` | Main Spotify interface with album art |
| `ProgressBar.vue` | Track progress with seek |
| `PlaybackControls.vue` | Play/pause/skip controls |

**Composables:**
- `usePlaybackProgress.js` - Progress tracking
- `useSpotifyControl.js` - Control actions

---

## Radio Components (`components/radio/`)

| Component | Description |
|-----------|-------------|
| `RadioSource.vue` | Main radio interface |
| `FavoritesView.vue` | Favorite stations list |
| `SearchView.vue` | Station search (RadioBrowser API) |
| `RadioScreensaver.vue` | Screensaver mode |
| `SkeletonStationCard.vue` | Loading skeleton |

---

## Podcast Components (`components/podcasts/`)

| Component | Description |
|-----------|-------------|
| `PodcastSource.vue` | Main container with navigation |
| `HomeView.vue` | Curated recommendations |
| `SearchView.vue` | Podcast search interface |
| `SubscriptionsView.vue` | User subscriptions |
| `GenreView.vue` | Browse by genre |
| `QueueView.vue` | Playback queue |
| `PodcastCard.vue` | Podcast card component |
| `EpisodeCard.vue` | Episode card component |
| `ProgressBar.vue` | Playback progress with seek |
| `PodcastDetails.vue` | Podcast detail view |
| `EpisodeDetails.vue` | Episode detail view |
| `SkeletonPodcastCard.vue` | Loading skeleton |
| `SkeletonPodcastDetails.vue` | Loading skeleton |
| `SkeletonEpisodeCard.vue` | Loading skeleton |
| `SkeletonEpisodeDetails.vue` | Loading skeleton |

---

## Multiroom Components (`components/multiroom/`)

| Component | Description |
|-----------|-------------|
| `MultiroomControl.vue` | Main multiroom panel |
| `MultiroomModal.vue` | Multiroom configuration modal |
| `MultiroomItem.vue` | Individual client item |
| `ClientEdit.vue` | Client configuration editor |

---

## Settings Components (`components/settings/`)

### Core
| Component | Description |
|-----------|-------------|
| `SettingsModal.vue` | Main settings modal |
| `SettingsCategory.vue` | Category wrapper |

### Categories (`components/settings/categories/`)
| Component | Description |
|-----------|-------------|
| `ApplicationsSettings.vue` | App dock configuration |
| `DspSettings.vue` | DSP/equalizer settings |
| `InfoSettings.vue` | System information |
| `LanguageSettings.vue` | Language selection |
| `MultiroomSettings.vue` | Multiroom configuration |
| `PodcastSettings.vue` | Podcast API credentials |
| `ScreenSettings.vue` | Screen timeout/brightness |
| `SpotifySettings.vue` | Spotify configuration |
| `UpdateManager.vue` | Software updates |
| `VolumeSettings.vue` | Volume limits & steps |

### Radio Settings (`components/settings/categories/radio/`)
| Component | Description |
|-----------|-------------|
| `RadioSettings.vue` | Radio configuration |
| `ManageStation.vue` | Custom station editor |

---

## DSP Components (`components/settings/categories/dsp/`)

| Component | Description |
|-----------|-------------|
| `AdvancedDsp.vue` | Advanced DSP panel |
| `ParametricEQ.vue` | Parametric equalizer |
| `EQBand.vue` | Individual EQ band control |
| `LevelMeters.vue` | Audio level meters |
| `LevelMeter.vue` | Single level meter |
| `ZoneList.vue` | Zone management list |
| `ZoneEdit.vue` | Zone editor |
| `ItemSelector.vue` | Client/zone selector |
| `PresetSaveDialog.vue` | Preset save dialog |

---

## UI Components (`components/ui/`)

### Layout & Navigation
| Component | Description |
|-----------|-------------|
| `Dock.vue` | Bottom navigation dock |
| `Modal.vue` | Modal dialog wrapper |
| `ModalHeader.vue` | Modal header with close |

### Form Controls
| Component | Description |
|-----------|-------------|
| `Button.vue` | Button component |
| `Toggle.vue` | Toggle switch |
| `Radio.vue` | Radio button |
| `Dropdown.vue` | Dropdown select |
| `InputText.vue` | Text input |
| `RangeSlider.vue` | Single range slider |
| `DoubleRangeSlider.vue` | Dual range slider |

### Display
| Component | Description |
|-----------|-------------|
| `VolumeBar.vue` | Volume indicator overlay |
| `LoadingSpinner.vue` | Loading indicator |
| `Logo.vue` | Milo logo |
| `SvgIcon.vue` | SVG icon wrapper |
| `MessageContent.vue` | Message display |
| `ListItemButton.vue` | List item button |

---

## Pinia Stores (`stores/`)

| Store | Size | Description |
|-------|------|-------------|
| `unifiedAudioStore.js` | 9KB | Central audio state (source, volume, routing) |
| `dspStore.js` | 47KB | DSP/equalizer state (filters, presets, zones) |
| `radioStore.js` | 22KB | Radio stations & playback |
| `podcastStore.js` | 17KB | Podcast subscriptions & playback |
| `multiroomStore.js` | 14KB | Multiroom/Snapcast state |
| `clientRegistryStore.js` | 12KB | Client/zone registry |
| `settingsStore.js` | 10KB | Application settings |

---

## Composables (`composables/`)

| Composable | Description |
|------------|-------------|
| `useAnimatedHeight.js` | Height animation helper |
| `useHardwareConfig.js` | Hardware configuration |
| `useNavigationStack.js` | Navigation state |
| `useScreenActivity.js` | Screen activity tracking |
| `useSettingsAPI.js` | Settings API interactions |
| `useVirtualKeyboard.js` | Virtual keyboard state |

---

## Design Patterns

### Component Organization
- **Domain-based folders**: Components grouped by feature (audio, spotify, radio, etc.)
- **Shared UI primitives**: Reusable components in `ui/` folder
- **Settings modularity**: Each settings category is a separate component

### State Management
- **Pinia stores**: Centralized state with Composition API style
- **WebSocket sync**: Real-time state updates from backend
- **Schema validation**: Zod schemas for API response validation

### Communication
- **Props down, events up**: Standard Vue data flow
- **Store actions**: API calls through Pinia actions
- **WebSocket service**: Centralized WebSocket handling

---

## Localization

Supported languages:
- English (`locales/en.json`)
- French (`locales/fr.json`)

i18n integration via `services/i18n.js`
