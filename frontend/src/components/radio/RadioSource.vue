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
      <div class="radio-content">
        <!-- Favorites View -->
        <FavoritesView v-if="!isSearchMode" key="favorites" :is-loading="radioStore.loading"
          :current-station="radioStore.currentStation" :is-playing="isCurrentlyPlaying"
          :buffering-station-id="bufferingStationId" @play-station="playStation" />

        <!-- Search View -->
        <SearchView v-else key="search" :country-options="countryOptions" :genre-options="genreOptions"
          :current-station="radioStore.currentStation" :is-playing="isCurrentlyPlaying"
          :buffering-station-id="bufferingStationId" :is-loading="radioStore.loading" :has-error="radioStore.hasError"
          @search="handleSearch" @retry="retrySearch" @play-station="playStation" />
      </div>
    </template>

    <!-- Player slot: AudioPlayer component -->
    <template #player="{ playerWidth, isMobile }">
      <AudioPlayer v-if="radioStore.currentStation" :visible="shouldShowNowPlayingLayout" source="radio"
        :artwork="playerArtwork" :placeholder-artwork="placeholderImg" :title="playerTitle"
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
import { ref, computed, onMounted, onBeforeUnmount, watch } from 'vue'
import axios from 'axios'
import { useRadioStore } from '@/stores/radioStore'
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore'
import useWebSocket from '@/services/websocket'
import { useI18n } from '@/services/i18n'
import { logger } from '@/services/logger'
import { genreOptions as createGenreOptions } from '@/constants/music_genres'
import { countryOptions as createCountryOptions } from '@/constants/countries'
import { RADIO_PLAYER_HIDE_DELAY_MS } from '@/constants/audio_player'
import IconButton from '@/components/ui/IconButton.vue'
import Button from '@/components/ui/Button.vue'
import AudioPlayer from '@/components/audio/AudioPlayer.vue'
import AudioSourceLayout from '@/components/audio/AudioSourceLayout.vue'
import FavoritesView from './FavoritesView.vue'
import SearchView from './SearchView.vue'
import placeholderImg from '@/assets/radio/station-placeholder.jpg'

const radioStore = useRadioStore()
const unifiedStore = useUnifiedAudioStore()
const { on } = useWebSocket()
const { t } = useI18n()

// === STATE ===
const isSearchMode = ref(false)
const availableCountries = ref([]) // Dynamic list of available countries
const shouldShowNowPlayingLayout = ref(false) // Controls now-playing visibility, layout and animation
const stopTimer = ref(null) // Timer for hiding now-playing after stop

// === COMPUTED ===

// Playback state - Use unifiedStore.metadata.is_playing (backend source of truth)
const isCurrentlyPlaying = computed(() => {
  if (unifiedStore.systemState.active_source !== 'radio') {
    return false
  }
  return unifiedStore.systemState.metadata.is_playing || false
})

// Buffering state - Use unifiedStore.metadata.buffering (backend source of truth)
const isBuffering = computed(() => {
  if (unifiedStore.systemState.active_source !== 'radio') {
    return false
  }
  return unifiedStore.systemState.metadata.buffering || false
})

// ID of the buffering station (to display the spinner on the correct station)
const bufferingStationId = computed(() => {
  if (!isBuffering.value) {
    return null
  }
  return unifiedStore.systemState.metadata.station_id || null
})

// Station artwork URL
const stationArtwork = computed(() => {
  const favicon = radioStore.currentStation?.favicon
  if (!favicon) return null

  // Local image already hosted by the backend
  if (favicon.startsWith('/api/radio/images/')) {
    return favicon
  }

  // External image: use backend proxy to avoid CORS
  return `/api/radio/favicon?url=${encodeURIComponent(favicon)}`
})

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

  const genre = station.genre ? station.genre.charAt(0).toUpperCase() + station.genre.slice(1) : ''
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

  return createCountryOptions(t, availableCountries.value, t('radio.country'))
})

// Genre options for dropdown
const genreOptions = computed(() => {
  return createGenreOptions(t, t('radio.genre'))
})

// === NAVIGATION ===
async function openSearch() {
  logger.debug('radio', `Opening search mode. Available countries: ${availableCountries.value.length}`)

  // Set loading AND switch mode immediately to prevent showing favorites
  radioStore.loading = true
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
  // Reset filters
  radioStore.searchQuery = ''
  radioStore.countryFilter = ''
  radioStore.genreFilter = ''

  // Reload favorites if not in cache
  if (radioStore.favoriteStations.length === 0) {
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

// === NOW PLAYING VISIBILITY ===
// Watch plugin_state to show player when connected
watch(() => unifiedStore.systemState.plugin_state, (newState) => {
  const isRadioActive = unifiedStore.systemState.active_source === 'radio'

  if (isRadioActive && newState === 'connected') {
    // Show player when connected (with smooth entrance)
    if (stopTimer.value) {
      clearTimeout(stopTimer.value)
      stopTimer.value = null
    }
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        shouldShowNowPlayingLayout.value = true
      })
    })
  }
  // Note: Don't hide here when state becomes 'ready' - let the isCurrentlyPlaying watcher handle the delayed hide
}, { immediate: true })

// Watch active_source to hide immediately when switching to another source
watch(() => unifiedStore.systemState.active_source, (newSource) => {
  if (newSource !== 'radio') {
    // Different source active - hide immediately
    if (stopTimer.value) {
      clearTimeout(stopTimer.value)
      stopTimer.value = null
    }
    shouldShowNowPlayingLayout.value = false
  }
}, { immediate: true })

// Auto-hide player after delay when playback stops
watch(isCurrentlyPlaying, (isPlaying) => {
  if (stopTimer.value) {
    clearTimeout(stopTimer.value)
    stopTimer.value = null
  }

  // Start hide timer when playback stops but player is visible
  if (!isPlaying && shouldShowNowPlayingLayout.value) {
    stopTimer.value = setTimeout(() => {
      shouldShowNowPlayingLayout.value = false
    }, RADIO_PLAYER_HIDE_DELAY_MS)
  }
}, { immediate: true })

// === WEBSOCKET SYNC ===
// currentStation now reads directly from unifiedStore.systemState.metadata
// No need for manual sync here

on('radio', 'favorite_added', (event) => {
  if (event.data?.station_id) {
    radioStore.handleFavoriteEvent(event.data.station_id, true)
  }
})

on('radio', 'favorite_removed', (event) => {
  if (event.data?.station_id) {
    radioStore.handleFavoriteEvent(event.data.station_id, false)
  }
})

on('radio', 'favorite_modified', (event) => {
  if (event.data?.station) {
    radioStore.handleMetadataModified(event.data.station)
  }
})

// === AVAILABLE COUNTRIES ===
async function loadAvailableCountries() {
  try {
    const response = await axios.get('/api/radio/countries')
    availableCountries.value = response.data
  } catch (error) {
    console.error('❌ Error loading countries:', error)
    availableCountries.value = []
  }
}

// === LIFECYCLE ===
onMounted(async () => {
  logger.debug('radio', 'RadioSource mounted')

  await radioStore.loadStations(true) // Load only favorites at startup
  // currentStation now reads directly from unifiedStore - no manual sync needed
})

onBeforeUnmount(() => {
  // Clear stop timer
  if (stopTimer.value) {
    clearTimeout(stopTimer.value)
    stopTimer.value = null
  }
  // currentStation reads from unifiedStore - no need to clear local state
})
</script>

<style scoped>
::-webkit-scrollbar {
  display: none;
}

/* Radio content: wraps the views inside AudioSourceLayout's content slot */
.radio-content {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
  width: 100%;
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
