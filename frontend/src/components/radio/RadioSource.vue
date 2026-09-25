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
      <AudioPlayer v-if="station" :visible="shouldShowNowPlayingLayout" source="radio" :artwork="playerArtwork"
        @after-hide="onAfterHide"
        :fallback-name="station?.name" :title="playerTitle"
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
          <template v-if="track">
            <PlayerInfoText class="vertical-layout"
              :kicker="track.artwork ? station?.name : null"
              :kicker-icon="track.artwork ? stationArtwork : null"
              :kicker-fallback-name="track.artwork ? station?.name : null"
              :title="track.title" :secondary="track.artist" />
            <template v-if="!expanded">
              <p class="player-title text-body horizontal-layout">{{ track.title }}</p>
              <p class="player-subtitle text-body horizontal-layout">{{ track.artist }}</p>
            </template>
          </template>
          <template v-else>
            <PlayerInfoText class="vertical-layout" :title="station?.name" />
            <p v-if="!expanded" class="player-title text-body horizontal-layout">{{ station?.name }}</p>
          </template>
        </template>

        <!-- Mobile only: station icon sits behind (pinned left), the track artwork
             rides on top offset to the right and reveals in from the station's position
             (AudioPlayer widens the frame and does the overlap/animation when this slot
             is populated). Gated on the track cover: a recognized track without an
             image stays single-image (station) + title/artist text, no overlap. -->
        <template v-if="isMobile && track?.artwork" #artwork-badge>
          <LazyImage class="player-artwork-badge" :src="stationArtwork" :fallback-name="station?.name" alt="" />
        </template>

        <template #controls="{ expanded }">
          <div class="radio-controls" @click.stop>
            <!-- Desktop sidebar / mobile expanded sheet: full on-dark Button with
                 icon+text — NOT a ghost icon button, unlike podcast/music-library's
                 transport. Radio's own convention, kept unconditionally as-is. -->
            <div class="radio-controls-main vertical-layout">
              <Button variant="on-dark" :left-icon="canStop ? 'stop' : 'play'"
                :loading="isBuffering" @click="handlePlayPause">
                {{ canStop ? t('audioSources.radioSource.stopRadio') :
                  t('audioSources.radioSource.playRadio') }}
              </Button>
              <IconButton :icon="stationIsFavorite ? 'heart' : 'heartOff'" variant="on-dark" size="medium"
                @click="handleFavorite" />
            </div>
            <!-- Mobile docked mini-bar only: compact row has no room for a text button +
                 heart — collapse to the single play/pause/stop ghost icon. Wrapped in
                 .playback-controls so it picks up AudioPlayer.vue's mobile icon-shrink
                 rule scoped to that class. `expanded` skips it in the sheet invocation
                 entirely — CSS never shows .horizontal-layout inside the expanded card
                 anyway, so rendering it there would only be always-hidden markup. -->
            <div v-if="!expanded" class="playback-controls horizontal-layout">
              <IconButton :icon="canStop ? 'stop' : 'play'" variant="ghost" size="medium"
                class="transport-primary" :loading="isBuffering" @click="handlePlayPause" />
            </div>
          </div>
        </template>
      </AudioPlayer>
    </template>
  </AudioSourceLayout>
</template>

<script setup>
import { ref, computed } from 'vue'
import { apiCall } from '@/services/apiCall'
import { useRadioStore } from '@/stores/radioStore'
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore'
import { useSourcePlaybackVisibility } from '@/composables/useSourcePlaybackVisibility'
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
const { t, getCurrentLanguage } = useI18n()

// === PLAYBACK VISIBILITY ===
// The pane follows the station, and the station survives a stop: the backend
// publishes the one `resume_playback` would re-tune, so `currentStation` no
// longer goes null the instant playback ends. The snapshot this component used
// to keep — displayStation, held for `auto_stop_delay` then cleared 600 ms
// after the fade — was a copy of that fact on a third lifetime, and the only
// one of the three that did not survive a page reload.
//
// The recognised track needs no such treatment and never did: it annotates a
// stream that is running, so it goes when the stream goes and the player falls
// back to the station's own name and logo.
//
// Both below are plain reads of the store, not snapshots of it — the names are
// for the template, which mentions them twenty times over.
const track = computed(() => radioStore.trackInfo)

const {
  isPlaying: isCurrentlyPlaying, isBuffering,
  shouldShowPlayer: shouldShowNowPlayingLayout,
  displayed: station, onAfterHide
} = useSourcePlaybackVisibility('radio', {
  content: () => radioStore.currentStation
})

// A live stream has no pause: the source takes `stop` while a session runs
// (loading included) and `resume_playback` to re-tune the station it kept.
const controls = computed(() =>
  unifiedStore.systemState.source === 'radio' ? unifiedStore.systemState.controls : []
)
const canStop = computed(() => controls.value.includes('stop'))

const stationIsFavorite = computed(() =>
  station.value
    ? radioStore.favoriteStations.some(s => s.id === station.value.id)
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
  return radioStore.currentStation?.id || null
})

// Station favicon URL — empty when missing; AudioPlayer generates the inline
// SVG fallback from `:fallback-name` so the font cascades correctly.
const stationArtwork = computed(() => getFaviconUrl(station.value?.favicon))

// Player display: the recognised track when there is one, the station
// otherwise — the same two-layer rule the backend applies to fill the common
// floor, here in the shape this player draws.
const playerArtwork = computed(() => {
  if (track.value?.artwork) return track.value.artwork
  return stationArtwork.value
})

const playerTitle = computed(() => {
  if (track.value) return track.value.title
  return station.value?.name
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
  // The live station, not the latched one: this is a tap in the grid, and
  // during the player's leave the latch still names the station on its way out.
  if (radioStore.currentStation?.id === stationId && isCurrentlyPlaying.value) {
    await radioStore.stopPlayback()
  } else {
    await radioStore.playStation(stationId)
  }
}

async function handlePlayPause() {
  if (canStop.value) {
    await radioStore.stopPlayback()
  } else if (controls.value.includes('resume_playback')) {
    // Re-tunes the station the state is publishing — a stopped radio keeps it.
    await unifiedStore.sendCommand('radio', 'resume_playback')
  }
}

async function handleFavorite() {
  if (station.value) {
    await radioStore.toggleFavorite(station.value.id)
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
