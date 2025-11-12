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
          <Dropdown
            v-model="radioStore.countryFilter"
            :options="countryOptions"
            @change="handleSearch"
          />

          <Dropdown
            v-model="radioStore.genreFilter"
            :options="genreOptions"
            @change="handleSearch"
          />
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
            <Icon name="radio" :size="96" color="var(--color-background-glass)" />
            <p class="text-mono">{{ t('audioSources.radioSource.loadingStations') }}</p>
          </div>
        </div>

        <!-- Error state -->
        <div v-else-if="radioStore.hasError && displayedStations.length === 0" class="message-wrapper" :class="{
          'message-fading-in': messageState === 'fading-in',
          'message-fading-out': messageState === 'fading-out'
        }">
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
        <div v-else-if="displayedStations.length === 0" class="message-wrapper" :class="{
          'message-fading-in': messageState === 'fading-in',
          'message-fading-out': messageState === 'fading-out'
        }">
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
import Dropdown from '@/components/ui/Dropdown.vue';
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
const messageState = ref('idle'); // States: 'idle', 'fading-in', 'fading-out'
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

// Country options for dropdown
const countryOptions = computed(() => {
  const options = [{ label: t('audioSources.radioSource.allCountries'), value: '' }];

  if (availableCountries.value.length === 0) {
    options.push({ label: t('audioSources.radioSource.loadingCountries'), value: '', disabled: true });
  }

  availableCountries.value.forEach(country => {
    options.push({ label: country.name, value: country.name });
  });

  return options;
});

// Genre options for dropdown
const genreOptions = computed(() => {
  return [
    { label: t('audioSources.radioSource.allGenres'), value: '' },
    { label: 'Pop', value: 'pop' },
    { label: 'Rock', value: 'rock' },
    { label: 'News', value: 'news' },
    { label: 'Classical', value: 'classical' },
    { label: 'Talk', value: 'talk' },
    { label: 'Dance', value: 'dance' },
    { label: 'Oldies', value: 'oldies' },
    { label: '80s', value: '80s' },
    { label: 'Jazz', value: 'jazz' },
    { label: '90s', value: '90s' },
    { label: 'Electronic', value: 'electronic' },
    { label: 'Classic Rock', value: 'classic rock' },
    { label: 'Country', value: 'country' },
    { label: 'Pop Rock', value: 'pop rock' },
    { label: 'House', value: 'house' },
    { label: 'Alternative', value: 'alternative' },
    { label: 'Metal', value: 'metal' },
    { label: 'Soul', value: 'soul' },
    { label: 'Indie', value: 'indie' },
    { label: 'Chillout', value: 'chillout' },
    { label: 'Techno', value: 'techno' },
    { label: 'Folk', value: 'folk' },
    { label: 'Disco', value: 'disco' },
    { label: 'Ambient', value: 'ambient' },
    { label: 'Blues', value: 'blues' },
    { label: 'Alternative Rock', value: 'alternative rock' },
    { label: 'Rap', value: 'rap' },
    { label: 'HipHop', value: 'hiphop' },
    { label: 'Lounge', value: 'lounge' },
    { label: 'Trance', value: 'trance' },
    { label: 'Latin Pop', value: 'latin pop' },
    { label: '60s', value: '60s' },
    { label: 'EDM', value: 'edm' },
    { label: 'Smooth Jazz', value: 'smooth jazz' },
    { label: 'Reggaeton', value: 'reggaeton' },
    { label: 'Tropical', value: 'tropical' },
    { label: 'Hard Rock', value: 'hard rock' },
    { label: 'Reggae', value: 'reggae' },
    { label: 'RnB', value: 'rnb' },
    { label: 'Hip-Hop', value: 'hip-hop' },
    { label: 'Deep House', value: 'deep house' },
    { label: 'Schlager', value: 'schlager' },
    { label: '70s', value: '70s' },
    { label: 'Punk', value: 'punk' },
    { label: 'Urban', value: 'urban' },
    { label: 'Latin', value: 'latin' },
    { label: 'Latin Music', value: 'latin music' },
    { label: 'R&B', value: 'r&b' },
    { label: 'Eurodance', value: 'eurodance' },
    { label: '2010s', value: '2010s' },
    { label: '1990s', value: '1990s' },
    { label: 'Merengue', value: 'merengue' },
    { label: 'New Wave', value: 'new wave' },
    { label: 'Pop Dance', value: 'pop dance' },
    { label: 'Classic Jazz', value: 'classic jazz' },
    { label: 'Funk', value: 'funk' },
    { label: 'Grunge', value: 'grunge' },
    { label: 'Minimal', value: 'minimal' },
    { label: 'Ska', value: 'ska' },
    { label: 'Italo Disco', value: 'italo disco' },
    { label: 'Singer-Songwriter', value: 'singer-songwriter' },
    { label: 'Opera', value: 'opera' },
    { label: 'Americana', value: 'americana' },
    { label: 'Darkwave', value: 'darkwave' },
    { label: 'Afrobeats', value: 'afrobeats' },
    { label: 'Bossa Nova', value: 'bossa nova' },
    { label: 'Celtic', value: 'celtic' },
    { label: 'Lo-Fi', value: 'lo-fi' },
    { label: 'Nu Disco', value: 'nu disco' },
    { label: 'Acoustic', value: 'acoustic' },
    { label: 'Folk Rock', value: 'folk rock' },
    { label: 'Progressive Rock', value: 'progressive rock' },
    { label: 'Art Rock', value: 'art rock' },
    { label: 'Psychedelic Rock', value: 'psychedelic rock' },
    { label: 'Britpop', value: 'britpop' },
    { label: 'Drum And Bass', value: 'drum and bass' },
    { label: 'Dubstep', value: 'dubstep' },
    { label: 'Trap', value: 'trap' },
    { label: 'Tech House', value: 'tech house' },
    { label: 'Jazz Fusion', value: 'jazz fusion' },
    { label: 'Downtempo', value: 'downtempo' },
    { label: 'Chill', value: 'chill' },
    { label: 'New Age', value: 'new age' },
    { label: 'World Music', value: 'world music' },
    { label: 'Garage', value: 'garage' },
    { label: 'Progressive House', value: 'progressive house' },
    { label: 'Trip-Hop', value: 'trip-hop' },
    { label: 'Minimal Techno', value: 'minimal techno' },
    { label: 'Psychedelic', value: 'psychedelic' },
    { label: 'Power Metal', value: 'power metal' },
    { label: 'Thrash Metal', value: 'thrash metal' },
    { label: 'Death Metal', value: 'death metal' },
    { label: 'Hardcore', value: 'hardcore' },
    { label: 'Stoner Rock', value: 'stoner rock' },
    { label: 'Synthwave', value: 'synthwave' },
    { label: 'Smooth Lounge', value: 'smooth lounge' },
    { label: 'Dancehall', value: 'dancehall' },
    { label: 'Dub', value: 'dub' },
    { label: 'Roots', value: 'roots' },
    { label: 'Salsa', value: 'salsa' },
    { label: 'Bachata', value: 'bachata' }
  ];
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