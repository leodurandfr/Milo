# Vue Component Usage Report

**Date:** 2026-03-26
**Scope:** `frontend/src/components/`
**Total components found:** 95

---

## Summary

| Status | Count |
|--------|-------|
| Used (imported/referenced) | 94 |
| **NEVER used (flagged)** | **1** |

---

## UNUSED COMPONENTS

| # | Component | File Path | Notes |
|---|-----------|-----------|-------|
| 1 | **`SignalDots`** | `frontend/src/components/settings/categories/wifi/SignalDots.vue` | Never imported anywhere. Sibling `WifiSignal` is used instead in `NetworkSettings.vue` and `NetworkStep.vue`. Likely superseded. |

---

## Full Component Inventory

### Audio (`components/audio/`)

| Component | File | Used By |
|-----------|------|---------|
| `AudioPlayer` | `AudioPlayer.vue` | `RadioSource.vue`, `PodcastSource.vue` |
| `AudioPlayerFull` | `AudioPlayerFull.vue` | `AirPlaySource.vue`, `CDSource.vue`, `SpotifySource.vue` |
| `AudioScreensaver` | `AudioScreensaver.vue` | `MainView.vue` (async) |
| `AudioSourceLayout` | `AudioSourceLayout.vue` | `RadioSource.vue`, `PodcastSource.vue` |
| `AudioSourceStatus` | `AudioSourceStatus.vue` | `AudioSourceView.vue` |
| `AudioSourceView` | `AudioSourceView.vue` | `MainView.vue` |
| `ConnectProgressBar` | `ConnectProgressBar.vue` | `AudioPlayerFull.vue` |
| `PlaybackControls` | `PlaybackControls.vue` | `AudioPlayerFull.vue` |

### AirPlay (`components/airplay/`)

| Component | File | Used By |
|-----------|------|---------|
| `AirPlaySource` | `AirPlaySource.vue` | `AudioSourceView.vue` (async) |

### CD (`components/cd/`)

| Component | File | Used By |
|-----------|------|---------|
| `CDSource` | `CDSource.vue` | `AudioSourceView.vue` (async) |
| `TrackCard` | `TrackCard.vue` | `CDSource.vue` |

### Spotify (`components/spotify/`)

| Component | File | Used By |
|-----------|------|---------|
| `SpotifySource` | `SpotifySource.vue` | `AudioSourceView.vue` (async) |

### Equalizer (`components/equalizer/`)

| Component | File | Used By |
|-----------|------|---------|
| `EQBand` | `EQBand.vue` | `ParametricEQ.vue` |
| `EqualizerModal` | `EqualizerModal.vue` | `App.vue` (async) |
| `ItemSelector` | `ItemSelector.vue` | `EqualizerModal.vue` |
| `LevelMeter` | `LevelMeter.vue` | `LevelMeters.vue` |
| `LevelMeters` | `LevelMeters.vue` | `EqualizerModal.vue` |
| `ParametricEQ` | `ParametricEQ.vue` | `EqualizerModal.vue` |

### Multiroom (`components/multiroom/`)

| Component | File | Used By |
|-----------|------|---------|
| `MultiroomControl` | `MultiroomControl.vue` | `MultiroomModal.vue` |
| `MultiroomItem` | `MultiroomItem.vue` | `MultiroomControl.vue` |
| `MultiroomModal` | `MultiroomModal.vue` | `App.vue` (async) |

### Podcasts (`components/podcasts/`)

| Component | File | Used By |
|-----------|------|---------|
| `CredentialsRequired` | `CredentialsRequired.vue` | `PodcastSource.vue` |
| `EpisodeCard` | `EpisodeCard.vue` | `PodcastDetails.vue`, `SubscriptionsView.vue`, `QueueView.vue`, `SearchView.vue`, `HomeView.vue`, `EpisodeDetails.vue`, `CardsStyleGuide.vue` |
| `EpisodeDetails` | `EpisodeDetails.vue` | `PodcastSource.vue` |
| `GenreCard` | `GenreCard.vue` | `HomeView.vue` |
| `GenreView` | `GenreView.vue` | `PodcastSource.vue` |
| `HomeView` | `HomeView.vue` | `PodcastSource.vue` |
| `PodcastCard` | `PodcastCard.vue` | `PodcastDetails.vue`, `SubscriptionsView.vue`, `GenreView.vue`, `HomeView.vue`, `SearchView.vue`, `CardsStyleGuide.vue` |
| `PodcastDetails` | `PodcastDetails.vue` | `PodcastSource.vue` |
| `PodcastSource` | `PodcastSource.vue` | `AudioSourceView.vue` (async) |
| `ProgressBar` | `ProgressBar.vue` | `PodcastSource.vue` |
| `QueueView` | `QueueView.vue` | `PodcastSource.vue` |
| `SearchView` | `SearchView.vue` | `PodcastSource.vue` |
| `SkeletonEpisodeCard` | `SkeletonEpisodeCard.vue` | `SkeletonEpisodeDetails.vue`, `SkeletonPodcastDetails.vue`, `HomeView.vue`, `CardsStyleGuide.vue` |
| `SkeletonEpisodeDetails` | `SkeletonEpisodeDetails.vue` | `EpisodeDetails.vue` |
| `SkeletonPodcastCard` | `SkeletonPodcastCard.vue` | `SkeletonPodcastDetails.vue`, `HomeView.vue`, `CardsStyleGuide.vue` |
| `SkeletonPodcastDetails` | `SkeletonPodcastDetails.vue` | `PodcastDetails.vue` |
| `SubscriptionsView` | `SubscriptionsView.vue` | `PodcastSource.vue` |

### Radio (`components/radio/`)

| Component | File | Used By |
|-----------|------|---------|
| `FavoritesView` | `FavoritesView.vue` | `RadioSource.vue` |
| `RadioSource` | `RadioSource.vue` | `AudioSourceView.vue` (async) |
| `SearchView` | `SearchView.vue` | `RadioSource.vue` |
| `SkeletonStationCard` | `SkeletonStationCard.vue` | `FavoritesView.vue`, `StationCard.vue`, `CardsStyleGuide.vue` |
| `StationCard` | `StationCard.vue` | `FavoritesView.vue`, `SearchView.vue`, `RadioSettings.vue`, `CardsStyleGuide.vue` |

### Navigation (`components/navigation/`)

_None listed — navigation components included in UI section._

### Settings (`components/settings/`)

| Component | File | Used By |
|-----------|------|---------|
| `SectionHeader` | `SectionHeader.vue` | `MultiroomSettings.vue` |
| `SettingItem` | `SettingItem.vue` | Many settings views |
| `SettingsContainer` | `SettingsContainer.vue` | Many settings views |
| `SettingsModal` | `SettingsModal.vue` | `App.vue` (async), `MainView.vue` (async) |
| `SettingsSection` | `SettingsSection.vue` | Many settings and equalizer views |

### Settings / Categories (`components/settings/categories/`)

| Component | File | Used By |
|-----------|------|---------|
| `DockSettings` | `DockSettings.vue` | `SettingsModal.vue` |
| `HardwareSettings` | `HardwareSettings.vue` | `SettingsModal.vue` |
| `InfoSettings` | `InfoSettings.vue` | `SettingsModal.vue` |
| `LanguageSettings` | `LanguageSettings.vue` | `SettingsModal.vue` |
| `MacSettings` | `MacSettings.vue` | `SettingsModal.vue` |
| `NetworkSettings` | `NetworkSettings.vue` | `SettingsModal.vue` |
| `PodcastSettings` | `PodcastSettings.vue` | `SettingsModal.vue` |
| `ScreenSettings` | `ScreenSettings.vue` | `SettingsModal.vue` |
| `SpotifySettings` | `SpotifySettings.vue` | `SettingsModal.vue` |
| `UpdateManager` | `UpdateManager.vue` | `SettingsModal.vue` |
| `VolumeSettings` | `VolumeSettings.vue` | `SettingsModal.vue` |

### Settings / Multiroom (`components/settings/categories/multiroom/`)

| Component | File | Used By |
|-----------|------|---------|
| `ClientEdit` | `ClientEdit.vue` | `SettingsModal.vue` |
| `ConfigureSpeaker` | `ConfigureSpeaker.vue` | `SettingsModal.vue` |
| `MultiroomSettings` | `MultiroomSettings.vue` | `SettingsModal.vue` |
| `SpeakerListItem` | `SpeakerListItem.vue` | `ZoneEdit.vue`, `MultiroomSettings.vue` |
| `ZoneEdit` | `ZoneEdit.vue` | `SettingsModal.vue` |

### Settings / Radio (`components/settings/categories/radio/`)

| Component | File | Used By |
|-----------|------|---------|
| `ManageStation` | `ManageStation.vue` | `SettingsModal.vue` |
| `RadioSettings` | `RadioSettings.vue` | `SettingsModal.vue` |

### Settings / WiFi (`components/settings/categories/wifi/`)

| Component | File | Used By |
|-----------|------|---------|
| **`SignalDots`** | `SignalDots.vue` | **NOWHERE — UNUSED** |
| `WifiSignal` | `WifiSignal.vue` | `NetworkSettings.vue`, `NetworkStep.vue` |

### Setup (`components/setup/`)

| Component | File | Used By |
|-----------|------|---------|
| `AudioStep` | `AudioStep.vue` | `SetupWizard.vue` |
| `LanguageStep` | `LanguageStep.vue` | `SetupWizard.vue` |
| `NetworkStep` | `NetworkStep.vue` | `SetupWizard.vue` |
| `ScreenStep` | `ScreenStep.vue` | `SetupWizard.vue` |
| `SetupWizard` | `SetupWizard.vue` | `App.vue` (async) |
| `StepIndicator` | `StepIndicator.vue` | `SetupWizard.vue` |
| `SummaryStep` | `SummaryStep.vue` | `SetupWizard.vue` |
| `WelcomeStep` | `WelcomeStep.vue` | `SetupWizard.vue` |

### UI (`components/ui/`)

| Component | File | Used By |
|-----------|------|---------|
| `AppIcon` | `AppIcon.vue` | `Dock.vue`, `NavigationHeader.vue`, `AudioScreensaver.vue`, `WelcomeStep.vue`, `AudioSourceStatus.vue`, `AudioPlayerFull.vue`, `UpdateManager.vue`, `DockSettings.vue` |
| `Button` | `Button.vue` | Many components |
| `ButtonGroup` | `ButtonGroup.vue` | Multiple settings views |
| `Dock` | `Dock.vue` | `App.vue` |
| `DoubleRangeSlider` | `DoubleRangeSlider.vue` | `VolumeSettings.vue`, `UIComponentsGuide.vue` |
| `Dropdown` | `Dropdown.vue` | Many components |
| `IconButton` | `IconButton.vue` | Many components |
| `InputText` | `InputText.vue` | Many components |
| `LazyImage` | `LazyImage.vue` | `StationCard.vue`, `PodcastCard.vue`, `EpisodeCard.vue` |
| `ListItemButton` | `ListItemButton.vue` | Many components |
| `LoadingSpinner` | `LoadingSpinner.vue` | Many components |
| `Logo` | `Logo.vue` | `MainView.vue` |
| `MessageContent` | `MessageContent.vue` | Many components |
| `Modal` | `Modal.vue` | `App.vue`, `MainView.vue` |
| `NavigationHeader` | `NavigationHeader.vue` | `AudioSourceLayout.vue`, `EqualizerModal.vue`, `MultiroomModal.vue`, `SettingsModal.vue` |
| `NotificationBanner` | `NotificationBanner.vue` | `App.vue`, `DockSettings.vue` |
| `Radio` | `Radio.vue` | `ListItemButton.vue`, `UIComponentsGuide.vue` |
| `RangeSlider` | `RangeSlider.vue` | Many components |
| `SvgIcon` | `SvgIcon.vue` | Many components |
| `Toggle` | `Toggle.vue` | Many components |
| `ToggleSection` | `ToggleSection.vue` | Multiple settings views |
| `VirtualKeyboard` | `VirtualKeyboard.vue` | `App.vue` (async) |
| `VolumeBar` | `VolumeBar.vue` | `App.vue` |
