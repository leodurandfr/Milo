<!-- RadioSource.vue - Refactored Router Pattern -->
<template>
  <!-- The navigation, and the full player it expands into: BrowserSourceViews
       swaps the two. -->
  <BrowserSourceViews source="radio" :playback="playback">
    <template #navigation="{ bar }">
      <AudioSourceLayout :show-player="shouldShowNowPlayingLayout"
        :header-title="isSearchMode ? t('audioSources.radioSource.discoverTitle') : t('audioSources.radioSource.favoritesTitle')"
        :header-show-back="isSearchMode" :header-actions-key="isSearchMode ? 'search' : 'favorites'"
        :content-key="isSearchMode ? 'search' : 'favorites'" header-icon="radio"
        :player-mobile-height="144" gradient="radio" @header-back="closeSearch">
        <template v-if="!isSearchMode" #header-actions>
          <IconButton icon="search" @click="openSearch" />
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
          <!-- The bar reads what it draws from the state; the source adds
               what is its own: the station behind a track on the phone, and
               the favorite at the end of the transport. -->
          <AudioPlayer v-if="station" v-bind="bar" source="radio">
            <!-- Mobile only: station icon sits behind (pinned left), the track artwork
                 rides on top offset to the right and reveals in from the station's position
                 (AudioPlayer widens the frame and does the overlap/animation when this slot
                 is populated). Gated on the track cover: a recognized track without an
                 image stays single-image (station) + title/artist text, no overlap. -->
            <template v-if="isMobile && track?.artwork" #artwork-badge>
              <LazyImage class="player-artwork-badge" :src="stationArtwork" :fallback-name="station?.name" alt="" />
            </template>

            <template #transport-end="{ variant }">
              <IconButton :icon="stationIsFavorite ? 'heart' : 'heartOff'" :variant="variant" size="medium"
                :disabled="isCustomStation(station?.id)" @click="handleFavorite" />
            </template>
          </AudioPlayer>
        </template>
      </AudioSourceLayout>
    </template>

    <!-- The station's favorite is not a command, so it is this source's to add,
         after the transport — in the full player as on the bar, in the fill of
         the stop button beside it. -->
    <template #transport-end="{ variant }">
      <IconButton :icon="stationIsFavorite ? 'heart' : 'heartOff'" :variant="variant" size="medium"
        :disabled="isCustomStation(station?.id)" @click="handleFavorite" />
    </template>
  </BrowserSourceViews>
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
import AudioPlayer from '@/components/audio/AudioPlayer.vue'
import BrowserSourceViews from '@/components/audio/BrowserSourceViews.vue'
import AudioSourceLayout from '@/components/audio/AudioSourceLayout.vue'
import LazyImage from '@/components/ui/LazyImage.vue'
import FavoritesView from './FavoritesView.vue'
import SearchView from './SearchView.vue'
import { getFaviconUrl } from '@/utils/faviconUrl'
import { isCustomStation } from '@/utils/radioStation'

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

const playback = useSourcePlaybackVisibility('radio', {
  content: () => radioStore.currentStation
})
const {
  isPlaying: isCurrentlyPlaying,
  shouldShowPlayer: shouldShowNowPlayingLayout,
  displayed: station
} = playback

const stationIsFavorite = computed(() =>
  station.value
    ? radioStore.favoriteStations.some(s => s.id === station.value.id)
    : false
)

// === STATE ===
const isSearchMode = ref(false)
const availableCountries = ref([])

// ID of the buffering station (to display the spinner on the correct station).
// Not the delayed buffering flag: on the card just tapped, the spinner is the
// press's only acknowledgement.
const bufferingStationId = computed(() => {
  const { source, session } = unifiedStore.systemState
  if (source !== 'radio' || session?.phase !== 'loading') {
    return null
  }
  return radioStore.currentStation?.id || null
})

// Station favicon URL for the phone's badge — empty when missing, and the badge
// then draws the station's generated avatar from `fallback-name`.
const stationArtwork = computed(() => getFaviconUrl(station.value?.favicon))

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
  return createGenreOptions(getCurrentLanguage(), radioStore.genres, t('radio.genre'))
})

// === NAVIGATION ===
async function openSearch() {
  logger.debug('radio', `Opening search mode. Available countries: ${availableCountries.value.length}`)

  // Set loading AND switch mode immediately to prevent showing favorites
  radioStore.setLoading(true)
  isSearchMode.value = true

  // Genres are not awaited: the dropdown fills in when they arrive.
  if (radioStore.genres.length === 0) {
    radioStore.loadGenres()
  }

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
</style>
