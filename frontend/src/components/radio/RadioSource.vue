<!-- RadioSource.vue - Refactored Router Pattern -->
<template>
  <AudioSourceLayout :show-player="shouldShowNowPlayingLayout">
    <!-- Content slot: scrollable views -->
    <template #content>
      <div
        ref="radioContainer"
        class="radio-content stagger-1"
        :class="{ 'is-initial-animating': isInitialAnimating }"
      >
        <!-- ModalHeader: Favorites view -->
        <ModalHeader
          v-if="!isSearchMode"
          :title="t('audioSources.radioSource.favoritesTitle')"
          variant="background-neutral"
          icon="radio"
        >
          <template #actions="{ iconVariant }">
            <IconButton icon="search" :variant="iconVariant" @click="openSearch" />
          </template>
        </ModalHeader>

        <!-- ModalHeader: Search view -->
        <ModalHeader
          v-else
          :title="t('audioSources.radioSource.discoverTitle')"
          :show-back="true"
          variant="background-neutral"
          @back="closeSearch"
        />

        <!-- Favorites View -->
        <FavoritesView
          v-if="!isSearchMode"
          :has-now-playing="shouldShowNowPlayingLayout"
          :current-station="radioStore.currentStation"
          :is-playing="isCurrentlyPlaying"
          :buffering-station-id="bufferingStationId"
          :message-state="messageState"
          @play-station="playStation"
        />

        <!-- Search View -->
        <SearchView
          v-else
          :country-options="countryOptions"
          :genre-options="genreOptions"
          :current-station="radioStore.currentStation"
          :is-playing="isCurrentlyPlaying"
          :buffering-station-id="bufferingStationId"
          :is-loading="radioStore.loading"
          :has-error="radioStore.hasError"
          :transition-state="transitionState"
          :message-state="messageState"
          @search="handleSearch"
          @retry="retrySearch"
          @play-station="playStation"
        />
      </div>
    </template>

    <!-- Player slot: AudioPlayer component -->
    <template #player>
      <AudioPlayer
        v-if="radioStore.currentStation"
        :visible="shouldShowNowPlayingLayout"
        source="radio"
        :artwork="stationArtwork"
        :placeholder-artwork="placeholderImg"
        :title="radioStore.currentStation.name"
        :subtitle="stationMetadata"
        :is-playing="isCurrentlyPlaying"
        :is-loading="isBuffering"
      >
        <!-- Radio controls with favorite and play/stop -->
        <template #controls>
          <div class="radio-controls">
            <IconButton
              :icon="radioStore.currentStation.is_favorite ? 'heart' : 'heartOff'"
              variant="dark"
              @click="handleFavorite"
            />
            <Button
              v-if="!isMobile"
              variant="dark"
              :left-icon="isCurrentlyPlaying ? 'stop' : 'play'"
              :loading="isBuffering"
              @click="handlePlayPause"
            >
              {{ isCurrentlyPlaying ? t('audioSources.radioSource.stopRadio') : t('audioSources.radioSource.playRadio') }}
            </Button>
            <IconButton
              v-else
              :icon="isCurrentlyPlaying ? 'stop' : 'play'"
              variant="dark"
              :loading="isBuffering"
              @click="handlePlayPause"
            />
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
import { genreOptions as createGenreOptions } from '@/constants/music_genres'
import { countryOptions as createCountryOptions } from '@/constants/countries'
import ModalHeader from '@/components/ui/ModalHeader.vue'
import IconButton from '@/components/ui/IconButton.vue'
import Button from '@/components/ui/Button.vue'
import AudioPlayer from '@/components/audio/AudioPlayer.vue'
import AudioSourceLayout from '@/components/ui/AudioSourceLayout.vue'
import FavoritesView from './FavoritesView.vue'
import SearchView from './SearchView.vue'
import placeholderImg from '@/assets/radio/station-placeholder.jpg'

const radioStore = useRadioStore()
const unifiedStore = useUnifiedAudioStore()
const { on } = useWebSocket()
const { t } = useI18n()

// === STATE ===
const isSearchMode = ref(false)
const searchDebounceTimer = ref(null)
const availableCountries = ref([]) // Dynamic list of available countries
const transitionState = ref('idle') // States: 'idle', 'fading-out', 'loading', 'fading-in'
const messageState = ref('idle') // States: 'idle', 'fading-in', 'fading-out'
const isInitialAnimating = ref(true) // Track if initial spring animation is running
const shouldShowNowPlayingLayout = ref(false) // Controls now-playing visibility, layout and animation
const stopTimer = ref(null) // Timer for hiding now-playing after stop
const isMobile = ref(false) // Responsive detection for desktop vs mobile

// Reference for animations and scroll
const radioContainer = ref(null)

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
      { label: t('audioSources.radioSource.allCountries'), value: '' },
      { label: t('audioSources.radioSource.loadingCountries'), value: '', disabled: true }
    ]
  }

  return createCountryOptions(t, availableCountries.value, t('audioSources.radioSource.allCountries'))
})

// Genre options for dropdown
const genreOptions = computed(() => {
  return createGenreOptions(t, t('audioSources.radioSource.allGenres'))
})

// === NAVIGATION ===
async function openSearch() {
  console.log('🔍 Opening search mode. Available countries:', availableCountries.value.length)

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

// === TRANSITION ANIMATIONS ===
async function performTransition() {
  const hasStations = (radioStore.displayedStations || []).length > 0

  if (!hasStations) {
    // No stations: simple loading → results transition
    transitionState.value = 'loading'
    messageState.value = 'fading-in'
    await new Promise(resolve => setTimeout(resolve, 300))

    messageState.value = 'idle'
    await radioStore.loadStations(false)

    messageState.value = 'fading-out'
    await new Promise(resolve => setTimeout(resolve, 300))

    messageState.value = 'fading-in'
    transitionState.value = 'fading-in'
    await new Promise(resolve => setTimeout(resolve, 400))

    messageState.value = 'idle'
    transitionState.value = 'idle'
    return
  }

  // Complete animation sequence
  transitionState.value = 'fading-out'
  await new Promise(resolve => setTimeout(resolve, 300))

  transitionState.value = 'loading'
  messageState.value = 'fading-in'
  await new Promise(resolve => setTimeout(resolve, 300))

  messageState.value = 'idle'
  await radioStore.loadStations(false)

  messageState.value = 'fading-out'
  await new Promise(resolve => setTimeout(resolve, 300))

  messageState.value = 'fading-in'
  transitionState.value = 'fading-in'
  await new Promise(resolve => setTimeout(resolve, 400))

  messageState.value = 'idle'
  transitionState.value = 'idle'
}

function handleSearch() {
  if (searchDebounceTimer.value) {
    clearTimeout(searchDebounceTimer.value)
  }

  searchDebounceTimer.value = setTimeout(async () => {
    await performTransition()
  }, 300)
}

function retrySearch() {
  radioStore.loadStations(false, true)
}

// === INFINITE SCROLL ===
function handleScroll() {
  if (!radioContainer.value || !isSearchMode.value) return

  const { scrollTop, scrollHeight, clientHeight } = radioContainer.value
  const scrollPercentage = (scrollTop + clientHeight) / scrollHeight

  if (scrollPercentage > 0.8 && radioStore.hasMoreStations && !radioStore.loading) {
    console.log('📻 Scroll threshold reached, loading more...')
    radioStore.loadMore()
  }
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

// === POINTER SCROLL ===
let isDragging = false
let startY = 0
let startScrollTop = 0
let pointerId = null

function handlePointerDown(event) {
  if (!radioContainer.value) return

  const isSlider = event.target.closest('input[type="range"]')
  const isButton = event.target.closest('button')
  const isInput = event.target.closest('input, select, textarea')
  const isDropdown = event.target.closest('.dropdown')

  if (isSlider || isButton || isInput || isDropdown) {
    return
  }

  isDragging = true
  pointerId = event.pointerId
  startY = event.clientY
  startScrollTop = radioContainer.value.scrollTop
}

function handlePointerMove(event) {
  if (!isDragging || event.pointerId !== pointerId || !radioContainer.value) return

  const deltaY = Math.abs(startY - event.clientY)

  if (deltaY > 5) {
    if (!radioContainer.value.hasPointerCapture(event.pointerId)) {
      radioContainer.value.setPointerCapture(event.pointerId)
    }

    event.preventDefault()

    const scrollDelta = startY - event.clientY
    radioContainer.value.scrollTop = startScrollTop + scrollDelta
  }
}

function handlePointerUp(event) {
  if (event.pointerId === pointerId) {
    isDragging = false
    pointerId = null

    if (radioContainer.value && radioContainer.value.hasPointerCapture(event.pointerId)) {
      radioContainer.value.releasePointerCapture(event.pointerId)
    }
  }
}

// === NOW PLAYING VISIBILITY ===
watch(isCurrentlyPlaying, (isPlaying) => {
  if (stopTimer.value) {
    clearTimeout(stopTimer.value)
    stopTimer.value = null
  }

  if (isPlaying && radioStore.currentStation) {
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        shouldShowNowPlayingLayout.value = true
      })
    })
  } else if (!isPlaying && radioStore.currentStation) {
    stopTimer.value = setTimeout(() => {
      shouldShowNowPlayingLayout.value = false
    }, 5000)
  } else if (!radioStore.currentStation) {
    shouldShowNowPlayingLayout.value = false
  }
}, { immediate: true })

// Stable layout during station changes
watch(() => radioStore.currentStation, (newStation) => {
  if (newStation && (isCurrentlyPlaying.value || isBuffering.value)) {
    shouldShowNowPlayingLayout.value = true
  }
}, { immediate: true })

// === WEBSOCKET SYNC ===
watch(() => unifiedStore.systemState.metadata, (newMetadata) => {
  if (unifiedStore.systemState.active_source === 'radio' && newMetadata) {
    radioStore.updateFromWebSocket(newMetadata)
  }
}, { immediate: true, deep: true })

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
  console.log('📻 RadioSource mounted')

  await radioStore.loadStations(true) // Load only favorites at startup

  // Sync currentStation from current backend state
  if (unifiedStore.systemState.active_source === 'radio' && unifiedStore.systemState.metadata) {
    console.log('📻 Syncing currentStation from existing state on mount')
    radioStore.updateFromWebSocket(unifiedStore.systemState.metadata)
  }

  // Mobile detection
  const updateMediaQuery = () => {
    isMobile.value = window.matchMedia('(max-aspect-ratio: 4/3)').matches
  }
  updateMediaQuery()
  window.addEventListener('resize', updateMediaQuery)

  // Disable initial animation after spring completes
  setTimeout(() => {
    isInitialAnimating.value = false
  }, 500)

  // Add scroll listener for infinite scroll
  if (radioContainer.value) {
    radioContainer.value.addEventListener('scroll', handleScroll, { passive: true })

    // Add pointer event listeners only on desktop
    const isTouchDevice = window.matchMedia('(pointer: coarse)').matches
    if (!isTouchDevice) {
      radioContainer.value.addEventListener('pointerdown', handlePointerDown, { passive: false })
      radioContainer.value.addEventListener('pointermove', handlePointerMove, { passive: false })
      radioContainer.value.addEventListener('pointerup', handlePointerUp, { passive: false })
      radioContainer.value.addEventListener('pointercancel', handlePointerUp, { passive: false })
    }
  }
})

onBeforeUnmount(() => {
  // Clear stop timer
  if (stopTimer.value) {
    clearTimeout(stopTimer.value)
    stopTimer.value = null
  }

  // Clear current station
  radioStore.clearCurrentStation()

  if (radioContainer.value) {
    radioContainer.value.removeEventListener('scroll', handleScroll)

    const isTouchDevice = window.matchMedia('(pointer: coarse)').matches
    if (!isTouchDevice) {
      radioContainer.value.removeEventListener('pointerdown', handlePointerDown)
      radioContainer.value.removeEventListener('pointermove', handlePointerMove)
      radioContainer.value.removeEventListener('pointerup', handlePointerUp)
      radioContainer.value.removeEventListener('pointercancel', handlePointerUp)
    }
  }
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
  height: 100%;
}

/* Initial stagger animation */
.stagger-1 {
  opacity: 0;
  transform: translateY(var(--space-07));
  animation:
    stagger-transform var(--transition-spring) forwards,
    stagger-opacity 0.4s ease forwards;
  animation-delay: 0ms;
}

@keyframes stagger-transform {
  to {
    transform: translateY(0);
  }
}

@keyframes stagger-opacity {
  to {
    opacity: 1;
  }
}

.radio-content.is-initial-animating {
  overflow-y: visible !important;
}

/* Radio controls layout */
.radio-controls {
  display: flex;
  flex-direction: row-reverse;
  flex-wrap: nowrap;
  gap: var(--space-02);
  justify-content: space-between;
  z-index: 1;
  width: 100%;
}

.radio-controls .btn {
  width: 100%;
}

/* Mobile: compact controls on the right */
@media (max-aspect-ratio: 4/3) {
  .radio-controls {
    width: auto;
    justify-content: flex-end;
    gap: var(--space-01);
  }
}
</style>
