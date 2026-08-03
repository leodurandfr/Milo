<!-- RadioSource.vue - Refactored Router Pattern -->
<template>
  <AudioSourceLayout :show-player="shouldShowNowPlayingLayout"
    :header-title="isSearchMode ? t('audioSources.radioSource.discoverTitle') : t('audioSources.radioSource.favoritesTitle')"
    :header-show-back="isSearchMode" :header-actions-key="isSearchMode ? 'search' : 'favorites'"
    :content-key="isSearchMode ? 'search' : 'favorites'" header-variant="background-neutral" header-icon="radio"
    :player-mobile-height="144" gradient="radio" @header-back="closeSearch">
    <template v-if="!isSearchMode" #header-actions="{ iconVariant }">
      <IconButton icon="search" :variant="iconVariant" @click="openSearch" />
    </template>

    <!-- Content slot: scrollable views -->
    <template #content>
      <!-- Favorites View -->
      <FavoritesView v-if="!isSearchMode" key="favorites" :is-loading="radioStore.loading"
        :current-station="radioStore.currentStation" :is-playing="isCurrentlyPlaying"
        :buffering-station-id="bufferingStationId" @play-station="playStation" />

      <!-- Search View -->
      <SearchView v-else key="search" :country-options="countryOptions" :genre-options="genreOptions"
        :current-station="radioStore.currentStation" :is-playing="isCurrentlyPlaying"
        :buffering-station-id="bufferingStationId" :is-loading="radioStore.loading" :has-error="radioStore.hasError"
        :search-unavailable="radioStore.searchUnavailable" @search="handleSearch" @retry="retrySearch"
        @play-station="playStation" />
    </template>

    <template #player="{ isMobile }">
      <AudioPlayer v-if="displayStation" :visible="shouldShowNowPlayingLayout" source="radio" :artwork="playerArtwork"
        :fallback-name="displayStation?.name" :title="playerTitle"
        :is-playing="isCurrentlyPlaying" :is-loading="isBuffering">
        <!-- Track info: PlayerInfoText's vertical layout renders identically in the
             desktop sidebar and the mobile expanded sheet — same as podcast/music-library
             (nothing hides .vertical-layout inside the expanded card for this source).
             Kicker (station name + icon) only shows when the recognized track has
             artwork — a textless "Station Name" line with nothing to back it up reads
             as clutter, so a track with no artwork falls back to plain title/artist.
             The horizontal-layout title/subtitle pair is only ever relevant to the
             mobile docked mini-bar (CSS never shows .horizontal-layout inside the
             expanded card), so `expanded` skips rendering it there entirely instead
             of emitting always-hidden markup. -->
        <template #info="{ expanded }">
          <template v-if="displayTrackInfo">
            <PlayerInfoText class="vertical-layout"
              :kicker="displayTrackInfo.artwork ? displayStation?.name : null"
              :kicker-icon="displayTrackInfo.artwork ? stationArtwork : null"
              :kicker-fallback-name="displayTrackInfo.artwork ? displayStation?.name : null"
              :title="displayTrackInfo.title" :secondary="displayTrackInfo.artist" />
            <template v-if="!expanded">
              <p class="player-title text-body horizontal-layout">{{ displayTrackInfo.title }}</p>
              <p class="player-subtitle text-body horizontal-layout">{{ displayTrackInfo.artist }}</p>
            </template>
          </template>
          <template v-else>
            <PlayerInfoText class="vertical-layout" :title="displayStation?.name" />
            <p v-if="!expanded" class="player-title text-body horizontal-layout">{{ displayStation?.name }}</p>
          </template>
        </template>

        <!-- Mobile only: station icon sits behind (pinned left), the track artwork
             rides on top offset to the right and reveals in from the station's position
             (AudioPlayer widens the frame and does the overlap/animation when this slot
             is populated). Gated on the track cover: a recognized track without an
             image stays single-image (station) + title/artist text, no overlap. -->
        <template v-if="isMobile && displayTrackInfo?.artwork" #artwork-badge>
          <LazyImage class="player-artwork-badge" :src="stationArtwork" :fallback-name="displayStation?.name" alt="" />
        </template>

        <template #controls="{ expanded }">
          <div class="radio-controls" @click.stop>
            <!-- Desktop sidebar / mobile expanded sheet: full on-dark Button with
                 icon+text — NOT a ghost icon button, unlike podcast/music-library's
                 transport. Radio's own convention, kept unconditionally as-is. -->
            <div class="radio-controls-main vertical-layout">
              <Button variant="on-dark" :left-icon="isCurrentlyPlaying ? 'stop' : 'play'"
                :loading="isBuffering" @click="handlePlayPause">
                {{ isCurrentlyPlaying ? t('audioSources.radioSource.stopRadio') :
                  t('audioSources.radioSource.playRadio') }}
              </Button>
              <IconButton :icon="displayStationIsFavorite ? 'heart' : 'heartOff'" variant="on-dark" size="medium"
                @click="handleFavorite" />
            </div>
            <!-- Mobile docked mini-bar only: compact row has no room for a text button +
                 heart — collapse to the single play/pause/stop ghost icon. Wrapped in
                 .playback-controls so it picks up AudioPlayer.vue's mobile icon-shrink
                 rule scoped to that class. `expanded` skips it in the sheet invocation
                 entirely — CSS never shows .horizontal-layout inside the expanded card
                 anyway, so rendering it there would only be always-hidden markup. -->
            <div v-if="!expanded" class="playback-controls horizontal-layout">
              <IconButton :icon="isCurrentlyPlaying ? 'stop' : 'play'" variant="ghost" size="medium"
                :loading="isBuffering" @click="handlePlayPause" />
            </div>
          </div>
        </template>
      </AudioPlayer>
    </template>
  </AudioSourceLayout>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { apiCall } from '@/services/apiCall'
import { useRadioStore } from '@/stores/radioStore'
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore'
import { useSettingsStore } from '@/stores/settingsStore'
import { useSourcePlaybackVisibility } from '@/composables/useSourcePlaybackVisibility'
import { useTimer } from '@/composables/useTimer'
import { useI18n } from '@/services/i18n'
import { logger } from '@/services/logger'
import { genreOptions as createGenreOptions } from '@/constants/musicGenres'
import { countryOptions as createCountryOptions } from '@/constants/countries'
import IconButton from '@/components/ui/IconButton.vue'
import Button from '@/components/ui/Button.vue'
import AudioPlayer from '@/components/audio/AudioPlayer.vue'
import AudioSourceLayout from '@/components/audio/AudioSourceLayout.vue'
import PlayerInfoText from '@/components/audio/PlayerInfoText.vue'
import LazyImage from '@/components/ui/LazyImage.vue'
import FavoritesView from './FavoritesView.vue'
import SearchView from './SearchView.vue'
import { getFaviconUrl } from '@/utils/faviconUrl'

const radioStore = useRadioStore()
const unifiedStore = useUnifiedAudioStore()
const settingsStore = useSettingsStore()
const { t, getCurrentLanguage } = useI18n()
const timer = useTimer()

// === PLAYBACK VISIBILITY ===
// radioStore.currentStation goes null the instant the backend reports the
// source stopped (its metadata drops station_id), which would unmount the
// player immediately. Snapshot the last station into displayStation so the
// stopped player can linger and fade out instead of vanishing — cleared once
// the fade-out animation completes (onFadeOutStart below). Mirrors the podcast
// store's displayEpisode pattern.
const displayStation = ref(null)
const displayTrackInfo = ref(null)
watch(
  () => radioStore.currentStation,
  (station) => {
    if (!station) return
    // A different station's metadata invalidates the previous track overlay —
    // drop it so a stale cover/title can't linger over the new station while it
    // buffers (before its own track is recognized). Compared against the
    // snapshot, not the watcher's previous value: currentStation goes null on
    // stop, so `prev` would be null when the next station arrives.
    if (station.id !== displayStation.value?.id) displayTrackInfo.value = null
    displayStation.value = station
  },
  { immediate: true }
)

// The backend goes straight to 'ready' on a radio stop (no pause/auto-stop
// window). Keep the last station shown for the configured auto_stop_delay
// (seconds → ms), resolved at stop-time so it tracks the setting. This is a
// frontend-only persistence: source_state is already 'ready', so it does not
// survive a page reload — irrelevant on the kiosk appliance. 0 (auto-stop
// disabled) hides immediately.
const { isPlaying: isCurrentlyPlaying, isBuffering, shouldShowPlayer: shouldShowNowPlayingLayout } =
  useSourcePlaybackVisibility('radio', {
    stoppedLingerMs: () => (settingsStore.audioPlayback.auto_stop_delay || 0) * 1000,
    onFadeOutStart: () => {
      // Drop the station once the 600ms CSS fade-out has played — but only if
      // the player is still meant to be hidden. A stop→replay inside this
      // window re-shows the player; clearing then would blank a live station.
      timer.setTimeout(() => {
        if (!shouldShowNowPlayingLayout.value) {
          displayStation.value = null
          displayTrackInfo.value = null
        }
      }, 600)
    }
  })

// Mirror displayStation for the recognized track — but only while a track is
// actually recognized. Unlike the station (which must survive the stop →
// fade-out window so the player has something to show), a track that is gone
// must disappear at once: on stop, or when the song is simply no longer
// recognized while the station keeps playing. Both revert the player to the
// station's own image/name instead of pinning the last detected song.
// (ref declared with displayStation so its watch can reset it.)
watch(
  () => radioStore.trackInfo,
  (info) => {
    displayTrackInfo.value = info
  },
  { immediate: true }
)

// Reactive favorite state for the displayed station: the snapshot itself is
// frozen during the linger, so derive the heart icon from the live list.
const displayStationIsFavorite = computed(() =>
  displayStation.value
    ? radioStore.favoriteStations.some(s => s.id === displayStation.value.id)
    : false
)

// === STATE ===
const isSearchMode = ref(false)
const availableCountries = ref([])

// ID of the buffering station (to display the spinner on the correct station)
const bufferingStationId = computed(() => {
  if (!isBuffering.value) {
    return null
  }
  return unifiedStore.systemState.metadata?.station_id || null
})

// Station favicon URL — empty when missing; AudioPlayer generates the inline
// SVG fallback from `:fallback-name` so the font cascades correctly.
// Reads the displayStation snapshot so it survives the stop → fade-out window.
const stationArtwork = computed(() => getFaviconUrl(displayStation.value?.favicon))

// Player display: use the snapshotted track info when available (so it survives
// the stop → fade-out window), fallback to station info.
const playerArtwork = computed(() => {
  if (displayTrackInfo.value?.artwork) return displayTrackInfo.value.artwork
  return stationArtwork.value
})

const playerTitle = computed(() => {
  if (displayTrackInfo.value) return displayTrackInfo.value.title
  return displayStation.value?.name
})

const countryOptions = computed(() => {
  if (availableCountries.value.length === 0) {
    return [
      { label: t('radio.country'), value: '' },
      { label: t('audioSources.radioSource.loadingCountries'), value: '', disabled: true }
    ]
  }

  return createCountryOptions(getCurrentLanguage(), availableCountries.value, t('radio.country'))
})

const genreOptions = computed(() => {
  return createGenreOptions(getCurrentLanguage(), t('radio.genre'))
})

// === NAVIGATION ===
async function openSearch() {
  logger.debug('radio', `Opening search mode. Available countries: ${availableCountries.value.length}`)

  // Set loading AND switch mode immediately to prevent showing favorites
  radioStore.setLoading(true)
  isSearchMode.value = true

  // Load countries if not yet loaded
  if (availableCountries.value.length === 0) {
    await loadAvailableCountries()
  }

  // Load top 500 stations
  await radioStore.loadStations(false)
}

function closeSearch() {
  isSearchMode.value = false
  radioStore.resetFilters()

  // Reload favorites only if preload never completed (edge case: opened very early)
  if (!radioStore.favoritesInitialized) {
    radioStore.loadStations(true)
  }
}

// === SEARCH ===
async function handleSearch() {
  await radioStore.loadStations(false)
}

function retrySearch() {
  radioStore.loadStations(false)
}

// === PLAYBACK CONTROLS ===
async function playStation(stationId) {
  if (radioStore.currentStation?.id === stationId && isCurrentlyPlaying.value) {
    await radioStore.stopPlayback()
  } else {
    await radioStore.playStation(stationId)
  }
}

async function handlePlayPause() {
  if (isCurrentlyPlaying.value) {
    await radioStore.stopPlayback()
  } else if (displayStation.value) {
    // During the stopped-linger window currentStation is already null —
    // resume the station still shown in the player.
    await radioStore.playStation(displayStation.value.id)
  }
}

async function handleFavorite() {
  if (displayStation.value) {
    await radioStore.toggleFavorite(displayStation.value.id)
  }
}

// === AVAILABLE COUNTRIES ===
async function loadAvailableCountries() {
  const result = await apiCall.get('/api/radio/countries', {
    category: 'radio',
    message: 'Error loading countries',
  })
  availableCountries.value = result.ok ? result.data : []
}

// === LIFECYCLE ===
// Favorites are preloaded at app boot (App.vue → radioStore.preloadFavorites)
// No need to load them here — they're already available when this component mounts
</script>

<style scoped>
::-webkit-scrollbar {
  display: none;
}

/* The .radio-controls / .radio-controls-main layout lives in AudioPlayer.vue,
   in :deep() — this row is slotted into it, and the same row is re-authored by
   the gallery's SourceStage, which scoped CSS here could never reach. */
</style>
