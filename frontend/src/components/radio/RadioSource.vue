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
        :network-error="radioStore.networkError" @search="handleSearch" @retry="retrySearch"
        @play-station="playStation" />
    </template>

    <template #player="{ isMobile }">
      <AudioPlayer v-if="displayStation" :visible="shouldShowNowPlayingLayout" source="radio" :artwork="playerArtwork"
        :fallback-name="displayStation?.name" :title="playerTitle"
        :is-playing="isCurrentlyPlaying" :is-loading="isBuffering">
        <!-- Track info: station kicker + title/artist when Shazam recognized a track,
             station name alone otherwise. Desktop sidebar uses PlayerInfoText's vertical
             layout with a kicker; the expanded sheet reuses the same PlayerInfoText but
             with the kicker suppressed and its `expanded` prop bumping the typography to
             heading-1 title / heading-2 artist, matching AudioPlayerFull's hierarchy
             (Spotify/AirPlay/CD) so full-screen players stay visually consistent.
             Mobile mini-bar renders its own compact title/subtitle pair (horizontal
             layout) instead (station identity conveyed via the artwork-badge slot below). -->
        <template #info="{ expanded }">
          <template v-if="displayTrackInfo">
            <PlayerInfoText :class="expanded ? 'expanded-only' : 'vertical-layout'"
              :kicker="expanded ? null : displayStation?.name" :kicker-icon="expanded ? null : stationArtwork"
              :kicker-fallback-name="expanded ? null : displayStation?.name" :title="displayTrackInfo.title"
              :secondary="displayTrackInfo.artist" :expanded="expanded" />
            <template v-if="!expanded">
              <p class="player-title text-body horizontal-layout">{{ displayTrackInfo.title }}</p>
              <p class="player-subtitle text-body horizontal-layout">{{ displayTrackInfo.artist }}</p>
            </template>
          </template>
          <template v-else>
            <PlayerInfoText :class="expanded ? 'expanded-only' : 'vertical-layout'" :title="displayStation?.name"
              :expanded="expanded" />
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

        <template #controls>
          <div class="radio-controls" @click.stop>
            <Button variant="on-dark" :left-icon="isCurrentlyPlaying ? 'stop' : 'play'"
              :loading="isBuffering" @click="handlePlayPause">
              {{ isCurrentlyPlaying ? t('audioSources.radioSource.stopRadio') : t('audioSources.radioSource.playRadio')
              }}
            </Button>
            <IconButton :icon="displayStationIsFavorite ? 'heart' : 'heartOff'" variant="ghost" size="small"
              @click="handleFavorite" />
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
  (station, prev) => {
    if (!station) return
    displayStation.value = station
    // A different station's metadata invalidates the previous track overlay —
    // drop it so a stale cover/title can't linger over the new station while it
    // buffers (before its own track is recognized).
    if (prev && station.id !== prev.id) displayTrackInfo.value = null
  },
  { immediate: true }
)

// The backend goes straight to 'waiting' on a radio stop (no pause/auto-stop
// window). Keep the last station shown for the configured auto_stop_delay
// (seconds → ms), resolved at stop-time so it tracks the setting. This is a
// frontend-only persistence: source_state is already 'waiting', so it does not
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

// Mirror displayStation for the recognized track. trackInfo is derived from
// metadata and drops to null the instant the source stops, which would collapse
// the two-thumbnail layout to station-only before the player runs its exit
// transition. Snapshotting keeps the last track (image + text) shown so it fades
// out with the player, and lingers alongside the station. Cleared immediately
// when the song is merely no longer recognized while the station keeps playing
// (revert to station-only), on a station change (above), and on fade-out
// completion above. (ref declared with displayStation so that watch can reset it.)
watch(
  () => radioStore.trackInfo,
  (info) => {
    if (info) displayTrackInfo.value = info
    else if (isCurrentlyPlaying.value) displayTrackInfo.value = null
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

/* Radio controls layout */
.radio-controls {
  display: flex;
  flex-wrap: nowrap;
  gap: var(--space-02);
  justify-content: space-between;
  z-index: 1;
  width: 100%;
}

.radio-controls .btn {
  width: 100%;
}

/* Mobile: same controls as desktop, just not stretched to the sidebar's full width */
@media (max-aspect-ratio: 4/3) {
  .radio-controls {
    width: auto;
    justify-content: flex-end;
    gap: var(--space-02);
  }

  .radio-controls .btn {
    width: auto;
  }
}
</style>
