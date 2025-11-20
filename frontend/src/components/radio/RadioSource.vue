<!-- RadioSource.vue -->
<template>
  <div class="radio-source-wrapper">
    <div ref="radioContainer" class="radio-container stagger-1" :class="{
      'is-initial-animating': isInitialAnimating,
      'with-now-playing': shouldShowNowPlayingLayout
    }">

      <!-- ModalHeader: Favorites view -->
      <ModalHeader v-if="!isSearchMode" :title="t('audioSources.radioSource.favoritesTitle')" variant="background-neutral"
        icon="radio">
        <template #actions="{ iconVariant }">
          <IconButton icon="search" :variant="iconVariant" @click="openSearch" />
        </template>
      </ModalHeader>

      <!-- ModalHeader: Search view -->
      <ModalHeader v-else :title="t('audioSources.radioSource.discoverTitle')" :show-back="true" variant="background-neutral"
        @back="closeSearch">
      </ModalHeader>

      <!-- Search and filters (visible only in search mode) -->
      <div v-if="isSearchMode" class="search-section">
        <div class="filters">
          <InputText v-model="radioStore.searchQuery" :placeholder="t('audioSources.radioSource.searchPlaceholder')"
            size="small" icon="search" :icon-size="24" @update:modelValue="handleSearch" />
          <Dropdown v-model="radioStore.countryFilter" :options="countryOptions" size="small" @change="handleSearch" />

          <Dropdown v-model="radioStore.genreFilter" :options="genreOptions" size="small" @change="handleSearch" />
        </div>
      </div>

      <!-- List of stations -->
      <div class="stations-list">
        <!-- Loading state -->
        <div v-if="transitionState === 'loading' || radioStore.loading" class="message-wrapper" :class="{
          'message-fading-in': messageState === 'fading-in',
          'message-fading-out': messageState === 'fading-out'
        }">
          <div class="message-content">
            <SvgIcon name="radio" :size="96" color="var(--color-background-medium-16)" />
            <p class="text-mono">{{ t('audioSources.radioSource.loadingStations') }}</p>
          </div>
        </div>

        <!-- Error state -->
        <div v-else-if="radioStore.hasError && displayedStations.length === 0" class="message-wrapper" :class="{
          'message-fading-in': messageState === 'fading-in',
          'message-fading-out': messageState === 'fading-out'
        }">
          <div class="message-content">
            <SvgIcon name="stop" :size="96" color="var(--color-background-medium-16)" />
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
        <div v-else-if="displayedStations.length === 0" class="message-wrapper" :class="{
          'message-fading-in': messageState === 'fading-in',
          'message-fading-out': messageState === 'fading-out'
        }">
          <div class="message-content">
            <SvgIcon name="radio" :size="96" color="var(--color-background-medium-16)" />
            <p class="text-mono">{{ isSearchMode ? t('audioSources.radioSource.noStationsFound') :
              t('audioSources.radioSource.noFavorites') }}</p>
          </div>
        </div>

        <div v-else-if="transitionState !== 'loading'" class="stations-grid" :class="{
          'favorites-mode': !isSearchMode,
          'search-mode': isSearchMode,
          'transition-fading-out': transitionState === 'fading-out',
          'transition-fading-in': transitionState === 'fading-in',
          'has-now-playing': shouldShowNowPlayingLayout
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
            :is-loading="bufferingStationId === station.id" :show-controls="true" :show-country="true"
            @click="playStation(station.id)" @play="playStation(station.id)" />
        </div>

      </div>
    </div>

    <!-- Now Playing: Desktop - on the right of the container, Mobile - sticky at the bottom -->
    <div :class="[
      'now-playing-wrapper',
      {
        'has-station': shouldShowNowPlayingLayout
      }
    ]">
      <StationCard v-if="radioStore.currentStation" :station="radioStore.currentStation"
        variant="now-playing" :show-controls="true" :is-playing="isCurrentlyPlaying" :is-loading="isBuffering"
        @play="handlePlayPause" @favorite="handleFavorite" />
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
import { genreOptions as createGenreOptions } from '@/constants/music_genres';
import { countryOptions as createCountryOptions } from '@/constants/countries';
import ModalHeader from '@/components/ui/ModalHeader.vue';
import IconButton from '@/components/ui/IconButton.vue';
import Button from '@/components/ui/Button.vue';
import InputText from '@/components/ui/InputText.vue';
import Dropdown from '@/components/ui/Dropdown.vue';
import StationCard from '@/components/radio/StationCard.vue';
import SvgIcon from '@/components/ui/SvgIcon.vue';
import placeholderImg from '@/assets/radio/station-placeholder.jpg';

const radioStore = useRadioStore();
const unifiedStore = useUnifiedAudioStore();
const { on } = useWebSocket();
const { t } = useI18n();

const isSearchMode = ref(false);
const searchDebounceTimer = ref(null);
const availableCountries = ref([]); // Dynamic list of available countries
const transitionState = ref('idle'); // States: 'idle', 'fading-out', 'loading', 'fading-in'
const messageState = ref('idle'); // States: 'idle', 'fading-in', 'fading-out'
const isInitialAnimating = ref(true); // Track if initial spring animation is running
const shouldShowNowPlayingLayout = ref(false); // Controls now-playing visibility, layout and animation
const stopTimer = ref(null); // Timer for hiding now-playing after stop

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
  let stations = [];

  if (isSearchMode.value) {
    // Search mode: ONLY show search results from RadioBrowserAPI
    // Never show favoriteStations (which includes custom stations)
    stations = radioStore.displayedStations || [];

    // Sort by popularity (clicks) - descending order
    return [...stations].sort((a, b) => {
      const clicksA = a.votes || 0;
      const clicksB = b.votes || 0;
      return clicksB - clicksA; // Higher clicks first
    });
  } else {
    // Favorites mode: display all favorites (including custom stations)
    stations = radioStore.favoriteStations || [];

    // Sort alphabetically by station name (case-insensitive)
    return [...stations].sort((a, b) => {
      const nameA = (a.name || '').toLowerCase();
      const nameB = (b.name || '').toLowerCase();
      return nameA.localeCompare(nameB);
    });
  }
});


// Country options for dropdown
const countryOptions = computed(() => {
  if (availableCountries.value.length === 0) {
    // No countries loaded yet, show loading message
    return [
      { label: t('audioSources.radioSource.allCountries'), value: '' },
      { label: t('audioSources.radioSource.loadingCountries'), value: '', disabled: true }
    ];
  }

  // Use createCountryOptions helper to generate translated country names
  return createCountryOptions(t, availableCountries.value, t('audioSources.radioSource.allCountries'));
});

// Genre options for dropdown
const genreOptions = computed(() => {
  return createGenreOptions(t, t('audioSources.radioSource.allGenres'));
});

// === NAVIGATION ===
async function openSearch() {
  console.log('🔍 Opening search mode. Available countries:', availableCountries.value.length);

  // CRITICAL: Set loading AND switch mode immediately to prevent showing favorites
  radioStore.loading = true;
  isSearchMode.value = true;

  // Load countries if not yet loaded
  if (availableCountries.value.length === 0) {
    await loadAvailableCountries();
  }

  // Load top 500 stations (this will clear visibleStations immediately)
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

// === TRANSITION ANIMATIONS ===
async function performTransition() {
  // If the list is empty, no need for exit animation
  if (displayedStations.value.length === 0) {
    // 1. Fade in loading message
    transitionState.value = 'loading';
    messageState.value = 'fading-in';
    await new Promise(resolve => setTimeout(resolve, 300));

    // 2. Show loading message (keep it visible)
    messageState.value = 'idle';

    // 3. Load new stations
    await radioStore.loadStations(false);

    // 4. Fade out loading message
    messageState.value = 'fading-out';
    await new Promise(resolve => setTimeout(resolve, 300));

    // 5. Fade in results or empty message
    messageState.value = 'fading-in';
    transitionState.value = 'fading-in';
    await new Promise(resolve => setTimeout(resolve, 400));

    // 6. Return to idle
    messageState.value = 'idle';
    transitionState.value = 'idle';
    return;
  }

  // Complete animation sequence
  // 1. Fade out stations to top (300ms)
  transitionState.value = 'fading-out';
  await new Promise(resolve => setTimeout(resolve, 300));

  // 2. Fade in loading message
  transitionState.value = 'loading';
  messageState.value = 'fading-in';
  await new Promise(resolve => setTimeout(resolve, 300));

  // 3. Show loading message (keep it visible)
  messageState.value = 'idle';

  // 4. Load new stations
  await radioStore.loadStations(false);

  // 5. Fade out loading message
  messageState.value = 'fading-out';
  await new Promise(resolve => setTimeout(resolve, 300));

  // 6. Fade in results or empty message
  messageState.value = 'fading-in';
  transitionState.value = 'fading-in';
  await new Promise(resolve => setTimeout(resolve, 400));

  // 7. Return to normal state
  messageState.value = 'idle';
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
  if (scrollPercentage > 0.8 && radioStore.hasMoreStations && !radioStore.loading) {
    console.log('📻 Scroll threshold reached, loading more...');
    radioStore.loadMore();
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
  const isDropdown = event.target.closest('.dropdown');

  if (isSlider || isButton || isInput || isDropdown) {
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

// === NOW PLAYING VISIBILITY ===
// Show now-playing for 5s after stopping, CSS handles the fade-out animation
watch(isCurrentlyPlaying, (isPlaying) => {
  // Clear any existing timer
  if (stopTimer.value) {
    clearTimeout(stopTimer.value);
    stopTimer.value = null;
  }

  if (isPlaying && radioStore.currentStation) {
    // Playing: show with animation delay for CSS transition
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        shouldShowNowPlayingLayout.value = true;
      });
    });
  } else if (!isPlaying && radioStore.currentStation) {
    // Stopped with a station: keep showing for 5s before hiding
    stopTimer.value = setTimeout(() => {
      shouldShowNowPlayingLayout.value = false;
    }, 5000);
  } else if (!radioStore.currentStation) {
    // No station at all: hide everything immediately
    shouldShowNowPlayingLayout.value = false;
  }
}, { immediate: true });

// === STABLE LAYOUT DURING STATION CHANGES ===
// Ensure layout stays stable even when currentStation briefly becomes null during transitions
watch(() => radioStore.currentStation, (newStation) => {
  if (newStation && (isCurrentlyPlaying.value || isBuffering.value)) {
    // If a station appears and we're playing/buffering, lock the layout immediately
    shouldShowNowPlayingLayout.value = true;
  }
  // Note: Don't unlock here - let the isCurrentlyPlaying watcher handle unlocking
}, { immediate: true });

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
  // Clear stop timer
  if (stopTimer.value) {
    clearTimeout(stopTimer.value);
    stopTimer.value = null;
  }

  // Clear current station to prevent showing stale data when returning to Radio plugin
  radioStore.clearCurrentStation();

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
  max-width: 310px;
  opacity: 0;
  flex-shrink: 0;
  transform: translateX(100px);
  /* Disparition : ease-out (smooth exit) */
  transition:
    width 0.6s cubic-bezier(0.5, 0, 0, 1),
    transform 0.6s cubic-bezier(0.5, 0, 0, 1),
    opacity 0.6s cubic-bezier(0.5, 0, 0, 1);
}

.now-playing-wrapper.has-station {
  width: 310px;
  max-width: 310px;
  opacity: 1;
  transform: translateX(0);
  /* Apparition : spring bounce (dynamic entry) */
  transition:
    width var(--transition-spring),
    transform var(--transition-spring),
    opacity 0.4s ease-out;
}

/* Mobile: now-playing is position fixed, so the wrapper must be transparent */
@media (max-aspect-ratio: 4/3) {

  .now-playing-wrapper {
    display: contents;
  }

  /* Mobile: now-playing animation (show/hide) */
  .now-playing-wrapper :deep(.now-playing) {
    /* État initial : caché en bas */
    transform: translate(-50%, 120px);
    opacity: 0;
    transition:
      transform 0.6s cubic-bezier(0.5, 0, 0, 1),
      opacity 0.6s cubic-bezier(0.5, 0, 0, 1);
  }

  .now-playing-wrapper.has-station :deep(.now-playing) {
    /* État visible : position normale */
    transform: translate(-50%, 0);
    opacity: 1;
    transition:
      transform var(--transition-spring),
      opacity 0.4s ease-out;
  }
}

/* === LAYOUT === */
.radio-source-wrapper {
  display: flex;
  justify-content: center;
  width: 100%;
  height: 100%;
  /* gap: 24px; */
  padding: 0 var(--space-07);
  transition: all var(--transition-spring);

}

.radio-container {
  position: relative;
  width: 84%;
  max-height: 100%;
  display: flex;
  flex-direction: column;
  overflow-y: auto;
  padding: var(--space-07) 0;
  gap: var(--space-04);
  min-height: 0;
  flex-shrink: 0;
  touch-action: pan-y;
  /* Disparition : ease-out (smooth exit) - même timing que now-playing */
  transition: width 0.6s cubic-bezier(0.5, 0, 0, 1);
}

.radio-container.with-now-playing {
  width: calc(100% - 310px - 24px);
  margin-right: 24px;
  /* Apparition : spring bounce (dynamic entry) */
  transition: width var(--transition-spring);
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
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--space-01);
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

/* === MESSAGE ANIMATIONS === */
@keyframes messageFadeIn {
  from {
    opacity: 0;
    transform: translateY(20px);
  }

  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@keyframes messageFadeOut {
  from {
    opacity: 1;
    transform: translateY(0);
  }

  to {
    opacity: 0;
    transform: translateY(-20px);
  }
}

.message-wrapper.message-fading-in {
  animation: messageFadeIn 300ms ease-out forwards;
}

.message-wrapper.message-fading-out {
  animation: messageFadeOut 300ms ease-out forwards;
}


/* Mobile: Responsive adaptations */
@media (max-aspect-ratio: 4/3) {
  .radio-source-wrapper {
    padding: 0 var(--space-05);
  }



  .radio-container.with-now-playing {
    width: 100%;
    margin-right: 0;
  }

  .radio-container {
    width: 100%;
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
    grid-template-columns: repeat(1, 1fr);
  }
}
</style>