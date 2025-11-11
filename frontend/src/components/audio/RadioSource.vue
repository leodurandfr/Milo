<template>
  <div class="radio-source-wrapper">
    <div ref="radioContainer" class="radio-container stagger-1" :class="{ 'is-initial-animating': isInitialAnimating }">

      <!-- ModalHeader: Favorites view -->
      <ModalHeader v-if="!isSearchMode" :title="t('audioSources.radioSource.favoritesTitle')" variant="neutral"
        icon="radio">
        <template #actions>
          <CircularIcon icon="search" variant="light" @click="openSearch" />
        </template>
      </ModalHeader>

      <!-- ModalHeader: Search view -->
      <ModalHeader v-else :title="t('audioSources.radioSource.discoverTitle')" :show-back="true" variant="neutral"
        @back="closeSearch">
      </ModalHeader>

      <!-- Search and filters (visible only in search mode) -->
      <div v-if="isSearchMode" class="search-section">
        <div class="filters">
          <InputText v-model="radioStore.searchQuery" :placeholder="t('audioSources.radioSource.searchPlaceholder')"
            input-class="filter-input search-input text-body-small" @update:modelValue="handleSearch" />
          <select v-model="radioStore.countryFilter" class="filter-input filter-select text-body-small"
            @change="handleSearch">
            <option value="">{{ t('audioSources.radioSource.allCountries') }}</option>
            <option v-if="availableCountries.length === 0" disabled>{{ t('audioSources.radioSource.loadingCountries')
              }}
            </option>
            <option v-for="country in availableCountries" :key="country.name" :value="country.name">
              {{ country.name }}
            </option>
          </select>

          <select v-model="radioStore.genreFilter" class="filter-input filter-select text-body-small"
            @change="handleSearch">
            <option value="">{{ t('audioSources.radioSource.allGenres') }}</option>
            <option value="pop">Pop</option>
            <option value="rock">Rock</option>
            <option value="news">News</option>
            <option value="classical">Classical</option>
            <option value="talk">Talk</option>
            <option value="dance">Dance</option>
            <option value="oldies">Oldies</option>
            <option value="80s">80s</option>
            <option value="jazz">Jazz</option>
            <option value="90s">90s</option>
            <option value="electronic">Electronic</option>
            <option value="classic rock">Classic Rock</option>
            <option value="country">Country</option>
            <option value="pop rock">Pop Rock</option>
            <option value="house">House</option>
            <option value="alternative">Alternative</option>
            <option value="metal">Metal</option>
            <option value="soul">Soul</option>
            <option value="indie">Indie</option>
            <option value="chillout">Chillout</option>
            <option value="techno">Techno</option>
            <option value="folk">Folk</option>
            <option value="disco">Disco</option>
            <option value="ambient">Ambient</option>
            <option value="blues">Blues</option>
            <option value="alternative rock">Alternative Rock</option>
            <option value="rap">Rap</option>
            <option value="hiphop">HipHop</option>
            <option value="lounge">Lounge</option>
            <option value="trance">Trance</option>
            <option value="latin pop">Latin Pop</option>
            <option value="60s">60s</option>
            <option value="edm">EDM</option>
            <option value="smooth jazz">Smooth Jazz</option>
            <option value="reggaeton">Reggaeton</option>
            <option value="tropical">Tropical</option>
            <option value="hard rock">Hard Rock</option>
            <option value="reggae">Reggae</option>
            <option value="rnb">RnB</option>
            <option value="hip-hop">Hip-Hop</option>
            <option value="deep house">Deep House</option>
            <option value="schlager">Schlager</option>
            <option value="70s">70s</option>
            <option value="punk">Punk</option>
            <option value="urban">Urban</option>
            <option value="latin">Latin</option>
            <option value="latin music">Latin Music</option>
            <option value="r&b">R&B</option>
            <option value="eurodance">Eurodance</option>
            <option value="2010s">2010s</option>
            <option value="1990s">1990s</option>
            <option value="merengue">Merengue</option>
            <option value="new wave">New Wave</option>
            <option value="pop dance">Pop Dance</option>
            <option value="classic jazz">Classic Jazz</option>
            <option value="funk">Funk</option>
            <option value="grunge">Grunge</option>
            <option value="minimal">Minimal</option>
            <option value="ska">Ska</option>
            <option value="italo disco">Italo Disco</option>
            <option value="singer-songwriter">Singer-Songwriter</option>
            <option value="opera">Opera</option>
            <option value="americana">Americana</option>
            <option value="darkwave">Darkwave</option>
            <option value="afrobeats">Afrobeats</option>
            <option value="bossa nova">Bossa Nova</option>
            <option value="celtic">Celtic</option>
            <option value="lo-fi">Lo-Fi</option>
            <option value="nu disco">Nu Disco</option>
            <option value="acoustic">Acoustic</option>
            <option value="folk rock">Folk Rock</option>
            <option value="progressive rock">Progressive Rock</option>
            <option value="art rock">Art Rock</option>
            <option value="psychedelic rock">Psychedelic Rock</option>
            <option value="britpop">Britpop</option>
            <option value="drum and bass">Drum And Bass</option>
            <option value="dubstep">Dubstep</option>
            <option value="trap">Trap</option>
            <option value="tech house">Tech House</option>
            <option value="jazz fusion">Jazz Fusion</option>
            <option value="downtempo">Downtempo</option>
            <option value="chill">Chill</option>
            <option value="new age">New Age</option>
            <option value="world music">World Music</option>
            <option value="garage">Garage</option>
            <option value="progressive house">Progressive House</option>
            <option value="trip-hop">Trip-Hop</option>
            <option value="minimal techno">Minimal Techno</option>
            <option value="psychedelic">Psychedelic</option>
            <option value="power metal">Power Metal</option>
            <option value="thrash metal">Thrash Metal</option>
            <option value="death metal">Death Metal</option>
            <option value="hardcore">Hardcore</option>
            <option value="stoner rock">Stoner Rock</option>
            <option value="synthwave">Synthwave</option>
            <option value="smooth lounge">Smooth Lounge</option>
            <option value="dancehall">Dancehall</option>
            <option value="dub">Dub</option>
            <option value="roots">Roots</option>
            <option value="salsa">Salsa</option>
            <option value="bachata">Bachata</option>
          </select>
        </div>
      </div>

      <!-- List of stations -->
      <div class="stations-list">
        <!-- Loading state -->
        <div v-if="transitionState === 'loading' || radioStore.loading" class="message-wrapper">
          <div class="message-content">
            <Icon name="radio" :size="96" color="var(--color-background-glass)" />
            <p class="text-mono">{{ t('audioSources.radioSource.loadingStations') }}</p>
          </div>
        </div>

        <!-- Error state -->
        <div v-else-if="radioStore.hasError && displayedStations.length === 0" class="message-wrapper">
          <div class="message-content">
            <Icon name="stop" :size="96" color="var(--color-background-glass)" />
            <p class="text-mono">{{ t('audioSources.radioSource.connectionError') }}</p>
            <p class="text-body-small" style="color: var(--color-text-secondary);">{{
              t('audioSources.radioSource.cannotLoadStations') }}
            </p>
            <Button variant="toggle" :active="false" @click="retrySearch">
              {{ t('audioSources.radioSource.retry') }}
            </Button>
          </div>
        </div>

        <!-- Empty state -->
        <div v-else-if="displayedStations.length === 0" class="message-wrapper">
          <div class="message-content">
            <Icon name="radio" :size="96" color="var(--color-background-glass)" />
            <p class="text-mono">{{ isSearchMode ? t('audioSources.radioSource.noStationsFound') :
              t('audioSources.radioSource.noFavorites') }}</p>
          </div>
        </div>

        <div v-else-if="transitionState !== 'loading'" class="stations-grid" :class="{
          'favorites-mode': !isSearchMode,
          'search-mode': isSearchMode,
          'transition-fading-out': transitionState === 'fading-out',
          'transition-fading-in': transitionState === 'fading-in',
          'has-now-playing': radioStore.currentStation
        }">
          <!-- Favorites mode: image-only display -->
          <StationCard v-if="!isSearchMode" v-for="station in displayedStations" :key="`fav-${station.id}`"
            :station="station" variant="image" :is-active="radioStore.currentStation?.id === station.id"
            :is-playing="radioStore.currentStation?.id === station.id && isCurrentlyPlaying"
            :is-loading="bufferingStationId === station.id" @click="playStation(station.id)" />

          <!-- Search mode: display with information -->
          <StationCard v-else v-for="station in displayedStations" :key="`search-${station.id}`" :station="station"
            variant="card" :is-active="radioStore.currentStation?.id === station.id"
            :is-playing="radioStore.currentStation?.id === station.id && isCurrentlyPlaying"
            :is-loading="bufferingStationId === station.id" :show-controls="true" @click="playStation(station.id)"
            @play="playStation(station.id)" />
        </div>

        <!-- "Load more" button -->
        <div v-if="hasMoreStations" class="load-more">
          <Button variant="toggle" :active="false" @click="loadMore">
            {{ t('audioSources.radioSource.loadMore') }} ({{ remainingStations }} {{
              t('audioSources.radioSource.remaining')
            }})
          </Button>
        </div>
      </div>
    </div>

    <!-- Now Playing: Desktop - on the right of the container, Mobile - sticky at the bottom -->
    <div :class="[
      'now-playing-wrapper',
      {
        'has-station': radioStore.currentStation,
        'first-appearance': radioStore.currentStation && isFirstAppearance
      }
    ]">
      <StationCard v-if="radioStore.currentStation" :class="{ 'first-appearance-mobile': isFirstAppearance }"
        :station="radioStore.currentStation" variant="now-playing" :show-controls="true"
        :is-playing="isCurrentlyPlaying" @play="handlePlayPause" @favorite="handleFavorite" />
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount, watch } from 'vue';
import axios from 'axios';
import { useRadioStore } from '@/stores/radioStore';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import useWebSocket from '@/services/websocket';
import { useI18n } from '@/services/i18n';
import ModalHeader from '@/components/ui/ModalHeader.vue';
import CircularIcon from '@/components/ui/CircularIcon.vue';
import AddStationModal from '@/components/settings/categories/AddStationModal.vue';
import Button from '@/components/ui/Button.vue';
import InputText from '@/components/ui/InputText.vue';
import StationCard from '@/components/audio/StationCard.vue';
import Icon from '@/components/ui/Icon.vue';
import placeholderImg from '@/assets/radio/station-placeholder.jpg';

const radioStore = useRadioStore();
const unifiedStore = useUnifiedAudioStore();
const { on } = useWebSocket();
const { t } = useI18n();

const isSearchMode = ref(false);
const searchDebounceTimer = ref(null);
const availableCountries = ref([]); // Dynamic list of available countries
const transitionState = ref('idle'); // States: 'idle', 'fading-out', 'loading', 'fading-in'
const isFirstAppearance = ref(true); // Track if this is the first time now-playing appears
const isInitialAnimating = ref(true); // Track if initial spring animation is running

// Reference for animations and scroll
const radioContainer = ref(null);

// Playback state - Use unifiedStore.metadata.is_playing (backend source of truth)
const isCurrentlyPlaying = computed(() => {
  // Check that the active source is Radio
  if (unifiedStore.systemState.active_source !== 'radio') {
    return false;
  }
  // Use backend state via WebSocket
  return unifiedStore.systemState.metadata.is_playing || false;
});

// Buffering state - Use unifiedStore.metadata.buffering (backend source of truth)
const isBuffering = computed(() => {
  // Check that the active source is Radio
  if (unifiedStore.systemState.active_source !== 'radio') {
    return false;
  }
  // Use backend state via WebSocket
  return unifiedStore.systemState.metadata.buffering || false;
});

// ID of the buffering station (to display the spinner on the correct station)
const bufferingStationId = computed(() => {
  if (!isBuffering.value) {
    return null;
  }
  return unifiedStore.systemState.metadata.station_id || null;
});

// Displayed stations - favorites or search results depending on mode
const displayedStations = computed(() => {
  if (isSearchMode.value) {
    return radioStore.displayedStations;
  } else {
    // Favorites mode: display all favorites (no pagination)
    return radioStore.favoriteStations;
  }
});

const hasMoreStations = computed(() => {
  if (isSearchMode.value) {
    return radioStore.hasMoreStations;
  }
  return false; // No pagination for favorites
});

const remainingStations = computed(() => {
  if (isSearchMode.value) {
    return radioStore.remainingStations;
  }
  return 0;
});

// === NAVIGATION ===
async function openSearch() {
  console.log('🔍 Opening search mode. Available countries:', availableCountries.value.length);

  // CRITICAL: Set loading BEFORE changing mode to prevent flash of "no stations" message
  radioStore.loading = true;
  isSearchMode.value = true;

  // Load countries if not yet loaded
  if (availableCountries.value.length === 0) {
    await loadAvailableCountries();
  }

  // Load top 500 stations
  await radioStore.loadStations(false);
}

function closeSearch() {
  isSearchMode.value = false;
  // Return to favorites mode - reset filters
  radioStore.searchQuery = '';
  radioStore.countryFilter = '';
  radioStore.genreFilter = '';

  // Only reload favorites if not already in cache
  if (radioStore.favoriteStations.length === 0) {
    radioStore.loadStations(true);
  }
}

// === ACTIONS ===
function loadMore() {
  radioStore.loadMore();
}

// === TRANSITION ANIMATIONS ===
async function performTransition() {
  // If the list is empty, no need for exit animation
  if (displayedStations.value.length === 0) {
    transitionState.value = 'loading';
    await radioStore.loadStations(false);
    transitionState.value = 'fading-in';
    await new Promise(resolve => setTimeout(resolve, 400));
    transitionState.value = 'idle';
    return;
  }

  // Complete animation sequence
  // 1. Fade out to top (300ms)
  transitionState.value = 'fading-out';
  await new Promise(resolve => setTimeout(resolve, 300));

  // 2. Display loading message
  transitionState.value = 'loading';

  // 3. Load new stations
  await radioStore.loadStations(false);

  // 4. Fade in from bottom (400ms)
  transitionState.value = 'fading-in';
  await new Promise(resolve => setTimeout(resolve, 400));

  // 5. Return to normal state
  transitionState.value = 'idle';
}

function handleSearch() {
  // Debounce to avoid too many API calls
  if (searchDebounceTimer.value) {
    clearTimeout(searchDebounceTimer.value);
  }

  searchDebounceTimer.value = setTimeout(async () => {
    // Use new transition function
    await performTransition();
  }, 300);
}

function retrySearch() {
  // Force refresh to retry after error
  radioStore.loadStations(false, true);
}

// === INFINITE SCROLL ===
function handleScroll() {
  if (!radioContainer.value || !isSearchMode.value) return;

  const { scrollTop, scrollHeight, clientHeight } = radioContainer.value;
  const scrollPercentage = (scrollTop + clientHeight) / scrollHeight;

  // Load more when reaching 80% of scroll
  if (scrollPercentage > 0.8 && hasMoreStations.value && !radioStore.loading) {
    console.log('📻 Scroll threshold reached, loading more...');
    loadMore();
  }
}

async function playStation(stationId) {
  // If station is already playing and clicked again, toggle play/pause
  if (radioStore.currentStation?.id === stationId && isCurrentlyPlaying.value) {
    await radioStore.stopPlayback();
  } else {
    await radioStore.playStation(stationId);
  }
}

async function togglePlayback() {
  if (isCurrentlyPlaying.value) {
    await radioStore.stopPlayback();
  } else if (radioStore.currentStation) {
    await radioStore.playStation(radioStore.currentStation.id);
  }
}

async function toggleFavorite(stationId) {
  await radioStore.toggleFavorite(stationId);
}

async function handlePlayPause() {
  if (isCurrentlyPlaying.value) {
    await radioStore.stopPlayback();
  } else if (radioStore.currentStation) {
    await radioStore.playStation(radioStore.currentStation.id);
  }
}

async function handleFavorite() {
  if (radioStore.currentStation) {
    await radioStore.toggleFavorite(radioStore.currentStation.id);
  }
}

function handleStationAdded(station) {
  console.log('✅ Station ajoutée:', station);
  // The station has been added to the store; it will appear automatically in the list
  // Optionally reload stations to be sure
  radioStore.loadStations(false);
}

// === POINTER SCROLL ===
let isDragging = false;
let startY = 0;
let startScrollTop = 0;
let pointerId = null;

function handlePointerDown(event) {
  if (!radioContainer.value) return;

  const isSlider = event.target.closest('input[type="range"]');
  const isButton = event.target.closest('button');
  const isInput = event.target.closest('input, select, textarea');

  if (isSlider || isButton || isInput) {
    return;
  }

  isDragging = true;
  pointerId = event.pointerId;
  startY = event.clientY;
  startScrollTop = radioContainer.value.scrollTop;
}

function handlePointerMove(event) {
  if (!isDragging || event.pointerId !== pointerId || !radioContainer.value) return;

  const deltaY = Math.abs(startY - event.clientY);

  if (deltaY > 5) {
    if (!radioContainer.value.hasPointerCapture(event.pointerId)) {
      radioContainer.value.setPointerCapture(event.pointerId);
    }

    event.preventDefault();

    const scrollDelta = startY - event.clientY;
    radioContainer.value.scrollTop = startScrollTop + scrollDelta;
  }
}

function handlePointerUp(event) {
  if (event.pointerId === pointerId) {
    isDragging = false;
    pointerId = null;

    if (radioContainer.value && radioContainer.value.hasPointerCapture(event.pointerId)) {
      radioContainer.value.releasePointerCapture(event.pointerId);
    }
  }
}

// === NOW PLAYING ANIMATION ===
// Watch for first station appearance and disable animation after it completes
watch(() => radioStore.currentStation, (newStation) => {
  if (newStation && isFirstAppearance.value) {
    // Wait for animation to complete (700ms spring + 200ms delay = 900ms)
    setTimeout(() => {
      isFirstAppearance.value = false;
    }, 900);
  }
});

// === WEBSOCKET SYNC ===
// Listen for metadata updates
watch(() => unifiedStore.systemState.metadata, (newMetadata) => {
  if (unifiedStore.systemState.active_source === 'radio' && newMetadata) {
    radioStore.updateFromWebSocket(newMetadata);
  }
}, { immediate: true, deep: true });

// Listen for favorite events
on('radio', 'favorite_added', (event) => {
  console.log('📻 Favorite added event:', event);
  if (event.data?.station_id) {
    radioStore.handleFavoriteEvent(event.data.station_id, true);
  }
});

on('radio', 'favorite_removed', (event) => {
  console.log('📻 Favorite removed event:', event);
  if (event.data?.station_id) {
    radioStore.handleFavoriteEvent(event.data.station_id, false);
  }
});

// === AVAILABLE COUNTRIES ===
async function loadAvailableCountries() {
  console.log('📍 Loading countries from API...');
  try {
    const response = await axios.get('/api/radio/countries');
    availableCountries.value = response.data;
    console.log(`📍 Loaded ${availableCountries.value.length} countries successfully`);
    console.log('📍 First 5 countries:', availableCountries.value.slice(0, 5));
  } catch (error) {
    console.error('❌ Error loading countries:', error);
    console.error('❌ Error details:', error.response?.data || error.message);
    // No fallback: empty list if API fails
    availableCountries.value = [];
    console.log('📍 Could not load countries from API');
  }
}

// === LIFECYCLE ===
onMounted(async () => {
  console.log('📻 RadioSource mounted');

  await radioStore.loadStations(true); // Load only favorites at startup

  // IMPORTANT: Sync currentStation from current backend state
  // in case a station is already playing
  if (unifiedStore.systemState.active_source === 'radio' && unifiedStore.systemState.metadata) {
    console.log('📻 Syncing currentStation from existing state on mount');
    radioStore.updateFromWebSocket(unifiedStore.systemState.metadata);
  }

  // Disable the initial animation flag after the spring animation finishes
  // Spring animation
  setTimeout(() => {
    isInitialAnimating.value = false;
  }, 500);

  // Add scroll listener for infinite scroll
  if (radioContainer.value) {
    radioContainer.value.addEventListener('scroll', handleScroll, { passive: true });

    // Add pointer event listeners only on desktop (pointer: fine)
    const isTouchDevice = window.matchMedia('(pointer: coarse)').matches;
    if (!isTouchDevice) {
      // On desktop, we need preventDefault for drag scroll
      radioContainer.value.addEventListener('pointerdown', handlePointerDown, { passive: false });
      radioContainer.value.addEventListener('pointermove', handlePointerMove, { passive: false });
      radioContainer.value.addEventListener('pointerup', handlePointerUp, { passive: false });
      radioContainer.value.addEventListener('pointercancel', handlePointerUp, { passive: false });
    }
  }
});

// Clean up listeners on unmount
onBeforeUnmount(() => {
  if (radioContainer.value) {
    radioContainer.value.removeEventListener('scroll', handleScroll);

    // Remove pointer event listeners
    const isTouchDevice = window.matchMedia('(pointer: coarse)').matches;
    if (!isTouchDevice) {
      radioContainer.value.removeEventListener('pointerdown', handlePointerDown);
      radioContainer.value.removeEventListener('pointermove', handlePointerMove);
      radioContainer.value.removeEventListener('pointerup', handlePointerUp);
      radioContainer.value.removeEventListener('pointercancel', handlePointerUp);
    }
  }
});
</script>

<style scoped>
::-webkit-scrollbar {
  display: none;
}

/* === SIMPLE AND NATURAL STAGGERING === */

/* Initial state: radio-container */
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

/* === NOW PLAYING WRAPPER === */

.now-playing-wrapper {
  width: 0;
  opacity: 0;
  overflow: hidden;
  flex-shrink: 0;
  transform: translateX(100px);
}

.now-playing-wrapper.has-station {
  width: 310px;
  opacity: 1;
  overflow: visible;
  transform: translateX(0);
}

/* Desktop: with station - FIRST appearance (animated) */
.now-playing-wrapper.has-station.first-appearance {
  transition:
    width var(--transition-spring) 200ms,
    transform var(--transition-spring) 200ms,
    opacity 0.4s ease 200ms;
}

/* Mobile: now-playing is position fixed, so the wrapper must be transparent */
@media (max-aspect-ratio: 4/3) {

  .now-playing-wrapper {
    display: contents;
  }

  /* Mobile stagger animation for now-playing (first appearance) */
  :deep(.now-playing.first-appearance-mobile) {
    opacity: 0;
    transform: translateX(-50%) translateY(var(--space-07));
    animation:
      stagger-transform-mobile var(--transition-spring) forwards,
      stagger-opacity 0.4s ease forwards;
    animation-delay: 200ms;
  }

  @keyframes stagger-transform-mobile {
    to {
      transform: translateX(-50%) translateY(0);
    }
  }
}

/* === LAYOUT === */
.radio-source-wrapper {
  display: flex;
  justify-content: center;
  width: 100%;
  height: 100%;
  gap: var(--space-06);
  padding: 0 var(--space-06);
  transition: all var(--transition-spring);

}

.radio-container {
  position: relative;
  width: 100%;
  max-width: 768px;
  max-height: 100%;
  display: flex;
  flex-direction: column;
  overflow-y: auto;
  padding: var(--space-07) 0;
  gap: var(--space-04);
  min-height: 0;
  flex: 1 1 auto;
  flex-shrink: 1;
  touch-action: pan-y;
}

/* Allow content to overflow during spring animation with bounce */
.radio-container.is-initial-animating {
  overflow-y: visible !important;
}



/* === SEARCH SECTION === */
.search-section {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

.filters {
  display: flex;
  gap: var(--space-01);
  color: var(--color-text-secondary);
}

.filter-input {
  width: 100%;
  flex: 1;
  padding: var(--space-03) var(--space-04);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-04);
  color: var(--color-text-secondary);
  background: var(--color-background-neutral);
  transition: border-color var(--transition-fast);
}

.filter-input:focus {
  outline: none;
  border-color: var(--color-brand);
}

.search-input {
  color: var(--color-text);
}

.search-input::placeholder {
  color: var(--color-text-secondary);
}


/* === STATIONS LIST === */
.stations-list {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

/* Message wrapper (loading, error, empty states) */
.message-wrapper {
  background: var(--color-background-neutral);
  border-radius: var(--radius-04);
}

.message-content {
  display: flex;
  min-height: 232px;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  gap: var(--space-04);
  padding: var(--space-05);
}

.message-content .text-mono,
.message-content .heading-2 {
  text-align: center;
  color: var(--color-text-secondary);
}

.message-content .text-body-small {
  text-align: center;
}

/* Mobile: increase height for better visibility */
@media (max-aspect-ratio: 4/3) {
  .message-content {
    min-height: 364px;
  }
}

.stations-grid {
  display: grid;
  padding-bottom: var(--space-07);
}

/* Search mode */
.stations-grid.search-mode {
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-01);
}

/* Favorites mode */
.stations-grid.favorites-mode {
  gap: var(--space-03);
  grid-template-columns: repeat(4, minmax(0, 1fr));
}


.load-more {
  padding-bottom: var(--space-06);
  text-align: center;
}


/* === TRANSITION ANIMATIONS === */
@keyframes fadeOutUp {
  from {
    opacity: 1;
    transform: translateY(0);
  }

  to {
    opacity: 0;
    transform: translateY(-20px);
  }
}

@keyframes fadeInUp {
  from {
    opacity: 0;
    transform: translateY(20px);
  }

  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.stations-grid.transition-fading-out {
  animation: fadeOutUp 300ms ease-out forwards;
}

.stations-grid.transition-fading-in {
  animation: fadeInUp 400ms ease-out forwards;
}


/* Mobile: Responsive adaptations */
@media (max-aspect-ratio: 4/3) {
  .radio-container {
    max-width: none;
    padding-bottom: calc(var(--space-04) + 80px);
    padding-top: var(--space-09);
  }
  
  .stations-grid.has-now-playing {
    padding-bottom: 144px;
  }

  .stations-grid.favorites-mode {
    grid-template-columns: repeat(3, 1fr);
  }

  .stations-grid.search-mode {
    grid-template-columns: 1fr;
  }

  .filters {
    flex-direction: column;
  }
}
</style>