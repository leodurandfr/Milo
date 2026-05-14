<!-- RadioSource.vue - Refactored Router Pattern -->
<template>
  <AudioSourceLayout :show-player="shouldShowNowPlayingLayout"
    :header-title="isSearchMode ? t('audioSources.radioSource.discoverTitle') : t('audioSources.radioSource.favoritesTitle')"
    :header-show-back="isSearchMode" :header-actions-key="isSearchMode ? 'search' : 'favorites'"
    :content-key="isSearchMode ? 'search' : 'favorites'" header-variant="background-neutral" header-icon="radio"
    :player-mobile-height="144" gradient="radio" @header-back="closeSearch">
    <!-- Header actions -->
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
          :network-error="radioStore.networkError"
          @search="handleSearch" @retry="retrySearch" @play-station="playStation" />
    </template>

    <!-- Player slot: AudioPlayer component -->
    <template #player="{ playerWidth, isMobile }">
      <AudioPlayer v-if="radioStore.currentStation" :visible="shouldShowNowPlayingLayout" source="radio"
        :artwork="playerArtwork" :fallback-name="radioStore.currentStation?.name" :title="playerTitle"
        :subtitle="playerSubtitle" :is-playing="isCurrentlyPlaying" :is-loading="isBuffering" :width="playerWidth">
        <!-- Track info: 3-line layout when Shazam recognized a track -->
        <template v-if="radioStore.trackInfo" #info>
          <!-- Desktop: 3-line layout -->
          <p class="player-title heading-2 radio-track--desktop">{{ radioStore.trackInfo.title }}</p>
          <p class="player-subtitle heading-4 radio-track--desktop">{{ radioStore.trackInfo.artist }}</p>
          <p class="player-subtitle text-mono radio-track--desktop">{{ radioStore.currentStation.name }}</p>
          <!-- Mobile: 2-line compact layout -->
          <p class="player-title heading-4 radio-track--mobile">{{ radioStore.trackInfo.title }} · {{ radioStore.trackInfo.artist }}</p>
          <p class="player-title text-mono radio-track--mobile radio-track-station">{{ radioStore.currentStation.name }}</p>
        </template>
        <!-- Radio controls with favorite and play/stop -->
        <template #controls>
          <div class="radio-controls">
            <Button v-if="!isMobile" variant="on-dark" :left-icon="isCurrentlyPlaying ? 'stop' : 'play'"
              :loading="isBuffering" @click="handlePlayPause">
              {{ isCurrentlyPlaying ? t('audioSources.radioSource.stopRadio') : t('audioSources.radioSource.playRadio')
              }}
            </Button>
            <!-- Play/stop IconButton for Mobile -->
            <IconButton v-else :icon="isCurrentlyPlaying ? 'stop' : 'play'" variant="on-dark" :loading="isBuffering"
              @click="handlePlayPause" />
            <IconButton :icon="radioStore.currentStation.is_favorite ? 'heart' : 'heartOff'" variant="on-dark"
              @click="handleFavorite" />
          </div>
        </template>
      </AudioPlayer>
    </template>
  </AudioSourceLayout>
</template>

<script setup>
import { ref, computed } from 'vue'
import axios from 'axios'
import { useRadioStore } from '@/stores/radioStore'
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore'
import { useSourcePlaybackVisibility } from '@/composables/useSourcePlaybackVisibility'
import { useI18n } from '@/services/i18n'
import { logger } from '@/services/logger'
import { genreOptions as createGenreOptions, getTranslatedGenreName } from '@/constants/musicGenres'
import { countryOptions as createCountryOptions } from '@/constants/countries'
import IconButton from '@/components/ui/IconButton.vue'
import Button from '@/components/ui/Button.vue'
import AudioPlayer from '@/components/audio/AudioPlayer.vue'
import AudioSourceLayout from '@/components/audio/AudioSourceLayout.vue'
import FavoritesView from './FavoritesView.vue'
import SearchView from './SearchView.vue'
import { getFaviconUrl } from '@/utils/faviconUrl'

const radioStore = useRadioStore()
const unifiedStore = useUnifiedAudioStore()
const { t, getCurrentLanguage } = useI18n()

// === PLAYBACK VISIBILITY ===
// Visual fade-out delay when radio stops — purely cosmetic, the backend
// handles auto-disconnect via audio.auto_disconnect_delay.
const HIDE_FADE_MS = 3000

const { isPlaying: isCurrentlyPlaying, isBuffering, shouldShowPlayer: shouldShowNowPlayingLayout } =
  useSourcePlaybackVisibility('radio', { hideDelayMs: HIDE_FADE_MS })

// === STATE ===
const isSearchMode = ref(false)
const availableCountries = ref([]) // Dynamic list of available countries

// ID of the buffering station (to display the spinner on the correct station)
const bufferingStationId = computed(() => {
  if (!isBuffering.value) {
    return null
  }
  return unifiedStore.systemState.metadata.station_id || null
})

// Station favicon URL — empty when missing; AudioPlayer generates the inline
// SVG fallback from `:fallback-name` so the font cascades correctly.
const stationArtwork = computed(() => getFaviconUrl(radioStore.currentStation?.favicon))

// Player display: use track info when available, fallback to station info
const playerArtwork = computed(() => {
  if (radioStore.trackInfo?.artwork) return radioStore.trackInfo.artwork
  return stationArtwork.value
})

const playerTitle = computed(() => {
  if (radioStore.trackInfo) return radioStore.trackInfo.title
  return radioStore.currentStation?.name
})

const playerSubtitle = computed(() => {
  if (radioStore.trackInfo) return radioStore.trackInfo.artist
  return stationMetadata.value
})

// Station metadata (genre + bitrate)
const stationMetadata = computed(() => {
  const station = radioStore.currentStation
  if (!station) return ''

  const genre = getTranslatedGenreName(getCurrentLanguage(), station.genre || '')
  const bitrate = station.bitrate

  // Both genre and bitrate
  if (genre && bitrate && bitrate > 0) {
    return `${genre} • ${bitrate} kbps`
  }

  // Only genre
  if (genre) {
    return genre
  }

  // Only bitrate
  if (bitrate && bitrate > 0) {
    return `${bitrate} kbps`
  }

  // Neither
  return ''
})

// Country options for dropdown
const countryOptions = computed(() => {
  if (availableCountries.value.length === 0) {
    return [
      { label: t('radio.country'), value: '' },
      { label: t('audioSources.radioSource.loadingCountries'), value: '', disabled: true }
    ]
  }

  return createCountryOptions(getCurrentLanguage(), availableCountries.value, t('radio.country'))
})

// Genre options for dropdown
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
  } else if (radioStore.currentStation) {
    await radioStore.playStation(radioStore.currentStation.id)
  }
}

async function handleFavorite() {
  if (radioStore.currentStation) {
    await radioStore.toggleFavorite(radioStore.currentStation.id)
  }
}

// === AVAILABLE COUNTRIES ===
async function loadAvailableCountries() {
  try {
    const response = await axios.get('/api/radio/countries')
    availableCountries.value = response.data
  } catch (error) {
    logger.error('radio', 'Error loading countries:', error)
    availableCountries.value = []
  }
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

/* Track info: mobile/desktop responsive variants */
.radio-track--mobile {
  display: none;
}

@media (max-aspect-ratio: 4/3) {
  .radio-track--desktop {
    display: none !important;
  }
  .radio-track--mobile {
    display: block !important;
  }
  .radio-track-station {
    color: var(--color-text-contrast-50) !important;
  }
}

/* Mobile: compact controls on the right */
@media (max-aspect-ratio: 4/3) {
  .radio-controls {
    width: auto;
    justify-content: flex-end;
    gap: var(--space-01);
    flex-direction: row-reverse;
  }
}
</style>
