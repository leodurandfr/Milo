<template>
  <div class="radio-source-wrapper">
    <div ref="radioContainer" class="radio-container stagger-1">

    <!-- ModalHeader : Vue Favoris -->
    <ModalHeader v-if="!isSearchMode" :title="t('audioSources.radioSource.favoritesTitle')" variant="neutral"
      icon="radio">
      <template #actions>
        <CircularIcon icon="search" variant="light" @click="openSearch" />
      </template>
    </ModalHeader>

    <!-- ModalHeader : Vue Recherche -->
    <ModalHeader v-else :title="t('audioSources.radioSource.discoverTitle')" :show-back="true" variant="neutral"
      @back="closeSearch">
    </ModalHeader>

    <!-- Recherche et filtres (visible uniquement en mode recherche) -->
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

    <!-- Liste des stations -->
    <div class="stations-list">
      <div v-if="transitionState === 'loading'" class="loading-state">
        <p>{{ t('audioSources.radioSource.loadingStations') }}</p>
      </div>

      <!-- Message d'erreur avec bouton réessayer -->
      <div v-else-if="radioStore.hasError && displayedStations.length === 0 && transitionState === 'idle'"
        class="error-state">
        <div class="error-icon">⚠️</div>
        <p class="heading-2">{{ t('audioSources.radioSource.connectionError') }}</p>
        <p class="text-body-small" style="color: var(--color-text-secondary);">{{
          t('audioSources.radioSource.cannotLoadStations') }}
        </p>
        <Button variant="toggle" :active="false" @click="retrySearch">
          {{ t('audioSources.radioSource.retry') }}
        </Button>
      </div>

      <div v-else-if="displayedStations.length === 0 && transitionState === 'idle'" class="empty-list">
        <div class="empty-icon"><img :src="placeholderImg" :alt="t('audioSources.radioSource.stationNoImage')" />
        </div>
        <p class="heading-2">{{ isSearchMode ? t('audioSources.radioSource.noStationsFound') :
          t('audioSources.radioSource.noFavorites') }}</p>
      </div>

      <div v-else-if="transitionState !== 'loading'" class="stations-grid" :class="{
        'favorites-mode': !isSearchMode,
        'search-mode': isSearchMode,
        'transition-fading-out': transitionState === 'fading-out',
        'transition-fading-in': transitionState === 'fading-in'
      }">
        <!-- Mode Favoris : affichage image seule -->
        <StationCard v-if="!isSearchMode" v-for="station in displayedStations" :key="`fav-${station.id}`"
          :station="station" variant="image" :is-active="radioStore.currentStation?.id === station.id"
          :is-playing="radioStore.currentStation?.id === station.id && isCurrentlyPlaying"
          :is-loading="bufferingStationId === station.id" @click="playStation(station.id)" />

        <!-- Mode Recherche : affichage avec informations -->
        <StationCard v-else v-for="station in displayedStations" :key="`search-${station.id}`" :station="station"
          variant="card" :is-active="radioStore.currentStation?.id === station.id"
          :is-playing="radioStore.currentStation?.id === station.id && isCurrentlyPlaying"
          :is-loading="bufferingStationId === station.id" :show-controls="true" @click="playStation(station.id)"
          @play="playStation(station.id)" />
      </div>

      <!-- Bouton "Charger plus" -->
      <div v-if="hasMoreStations" class="load-more">
        <Button variant="toggle" :active="false" @click="loadMore">
          {{ t('audioSources.radioSource.loadMore') }} ({{ remainingStations }} {{
            t('audioSources.radioSource.remaining')
          }})
        </Button>
      </div>
    </div>
    </div>

    <!-- Now Playing : Desktop - à droite du container, Mobile - sticky en bas -->
    <div :class="[
      'now-playing-wrapper',
      {
        'has-station': radioStore.currentStation,
        'first-appearance': radioStore.currentStation && isFirstAppearance
      }
    ]">
      <StationCard
        v-if="radioStore.currentStation"
        :class="{ 'first-appearance-mobile': isFirstAppearance }"
        :station="radioStore.currentStation"
        variant="now-playing"
        :show-controls="true"
        :is-playing="isCurrentlyPlaying"
        @play="handlePlayPause"
        @favorite="handleFavorite" />
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
import placeholderImg from '@/assets/radio/station-placeholder.jpg';

const radioStore = useRadioStore();
const unifiedStore = useUnifiedAudioStore();
const { on } = useWebSocket();
const { t } = useI18n();

const isSearchMode = ref(false);
const searchDebounceTimer = ref(null);
const availableCountries = ref([]); // Liste dynamique des pays disponibles
const transitionState = ref('idle'); // États: 'idle', 'fading-out', 'loading', 'fading-in'
const isFirstAppearance = ref(true); // Track if this is the first time now-playing appears

// Référence pour animations et scroll
const radioContainer = ref(null);

// État de lecture - Utiliser unifiedStore.metadata.is_playing (source de vérité backend)
const isCurrentlyPlaying = computed(() => {
  // Vérifier que la source active est bien Radio
  if (unifiedStore.systemState.active_source !== 'radio') {
    return false;
  }
  // Utiliser l'état du backend via WebSocket
  return unifiedStore.systemState.metadata.is_playing || false;
});

// État de buffering - Utiliser unifiedStore.metadata.buffering (source de vérité backend)
const isBuffering = computed(() => {
  // Vérifier que la source active est bien Radio
  if (unifiedStore.systemState.active_source !== 'radio') {
    return false;
  }
  // Utiliser l'état du backend via WebSocket
  return unifiedStore.systemState.metadata.buffering || false;
});

// ID de la station en buffering (pour afficher le spinner sur la bonne station)
const bufferingStationId = computed(() => {
  if (!isBuffering.value) {
    return null;
  }
  return unifiedStore.systemState.metadata.station_id || null;
});

// Stations affichées - favoris ou résultats de recherche selon le mode
const displayedStations = computed(() => {
  if (isSearchMode.value) {
    return radioStore.displayedStations;
  } else {
    // Mode favoris : afficher tous les favoris (pas de pagination)
    return radioStore.favoriteStations;
  }
});

const hasMoreStations = computed(() => {
  if (isSearchMode.value) {
    return radioStore.hasMoreStations;
  }
  return false; // Pas de pagination pour les favoris
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
  isSearchMode.value = true;

  // Charger les pays si pas encore chargés
  if (availableCountries.value.length === 0) {
    await loadAvailableCountries();
  }

  // Charger les stations top 500 (utilise le cache si déjà chargé)
  radioStore.loadStations(false);
}

function closeSearch() {
  isSearchMode.value = false;
  // Retour au mode favoris - réinitialiser les filtres
  radioStore.searchQuery = '';
  radioStore.countryFilter = '';
  radioStore.genreFilter = '';

  // Ne recharger les favoris que s'ils ne sont pas déjà en cache
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
  // Si la liste est vide, pas besoin d'animation de sortie
  if (displayedStations.value.length === 0) {
    transitionState.value = 'loading';
    await radioStore.loadStations(false);
    transitionState.value = 'fading-in';
    await new Promise(resolve => setTimeout(resolve, 400));
    transitionState.value = 'idle';
    return;
  }

  // Séquence complète d'animations
  // 1. Fade out vers le haut (300ms)
  transitionState.value = 'fading-out';
  await new Promise(resolve => setTimeout(resolve, 300));

  // 2. Afficher le message de chargement
  transitionState.value = 'loading';

  // 3. Charger les nouvelles stations
  await radioStore.loadStations(false);

  // 4. Fade in depuis le bas (400ms)
  transitionState.value = 'fading-in';
  await new Promise(resolve => setTimeout(resolve, 400));

  // 5. Retour à l'état normal
  transitionState.value = 'idle';
}

function handleSearch() {
  // Debounce pour éviter trop d'appels API
  if (searchDebounceTimer.value) {
    clearTimeout(searchDebounceTimer.value);
  }

  searchDebounceTimer.value = setTimeout(async () => {
    // Utiliser la nouvelle fonction de transition
    await performTransition();
  }, 300);
}

function retrySearch() {
  // Forcer un refresh pour réessayer après une erreur
  radioStore.loadStations(false, true);
}

// === SCROLL INFINI ===
function handleScroll() {
  if (!radioContainer.value || !isSearchMode.value) return;

  const { scrollTop, scrollHeight, clientHeight } = radioContainer.value;
  const scrollPercentage = (scrollTop + clientHeight) / scrollHeight;

  // Charger plus quand on atteint 80% du scroll
  if (scrollPercentage > 0.8 && hasMoreStations.value && !radioStore.loading) {
    console.log('📻 Scroll threshold reached, loading more...');
    loadMore();
  }
}

async function playStation(stationId) {
  // Si la station est déjà en cours et qu'on clique dessus, toggle play/pause
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
  // La station a été ajoutée au store, elle apparaîtra automatiquement dans la liste
  // On peut optionnellement recharger les stations pour être sûr
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

// === SYNCHRONISATION WEBSOCKET ===
// Écouter les mises à jour de métadonnées
watch(() => unifiedStore.systemState.metadata, (newMetadata) => {
  if (unifiedStore.systemState.active_source === 'radio' && newMetadata) {
    radioStore.updateFromWebSocket(newMetadata);
  }
}, { immediate: true, deep: true });

// Écouter les événements de favoris
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

// === PAYS DISPONIBLES ===
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
    // Pas de fallback : liste vide si l'API échoue
    availableCountries.value = [];
    console.log('📍 Could not load countries from API');
  }
}

// === LIFECYCLE ===
onMounted(async () => {
  console.log('📻 RadioSource mounted');

  await radioStore.loadStations(true); // Charger uniquement les favoris au démarrage

  // IMPORTANT: Synchroniser currentStation depuis l'état actuel du backend
  // au cas où une station est déjà en cours de lecture
  if (unifiedStore.systemState.active_source === 'radio' && unifiedStore.systemState.metadata) {
    console.log('📻 Syncing currentStation from existing state on mount');
    radioStore.updateFromWebSocket(unifiedStore.systemState.metadata);
  }

  // Ajouter l'écouteur de scroll pour le scroll infini
  if (radioContainer.value) {
    radioContainer.value.addEventListener('scroll', handleScroll, { passive: true });

    // Ajouter les pointer event listeners uniquement sur desktop (pointer: fine)
    const isTouchDevice = window.matchMedia('(pointer: coarse)').matches;
    if (!isTouchDevice) {
      // Sur desktop, on a besoin de preventDefault pour le drag scroll
      radioContainer.value.addEventListener('pointerdown', handlePointerDown, { passive: false });
      radioContainer.value.addEventListener('pointermove', handlePointerMove, { passive: false });
      radioContainer.value.addEventListener('pointerup', handlePointerUp, { passive: false });
      radioContainer.value.addEventListener('pointercancel', handlePointerUp, { passive: false });
    }
  }
});

// Nettoyer les écouteurs au démontage
onBeforeUnmount(() => {
  if (radioContainer.value) {
    radioContainer.value.removeEventListener('scroll', handleScroll);

    // Retirer les pointer event listeners
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

/* === STAGGERING SIMPLE ET NATUREL === */

/* État initial : radio-container */
.stagger-1 {
  opacity: 0;
  transform: translateY(var(--space-07));
  animation:
    stagger-transform var(--transition-spring) forwards,
    stagger-opacity 0.4s ease forwards;
  animation-delay: 0ms;
}

/* Animation spring pour le transform (radio-container - vertical) */
@keyframes stagger-transform {
  to {
    transform: translateY(0);
  }
}

/* Animation ease pour l'opacité */
@keyframes stagger-opacity {
  to {
    opacity: 1;
  }
}

/* === NOW PLAYING WRAPPER === */

/* Desktop : État de base - pas de station, prend 0 place dans le flex layout */
.now-playing-wrapper {
  width: 0;
  opacity: 0;
  overflow: hidden;
  flex-shrink: 0;
  transform: translateX(100px);
}

/* Desktop : État avec station - apparitions suivantes (instantané) */
.now-playing-wrapper.has-station {
  width: 310px;
  opacity: 1;
  overflow: visible;
  transform: translateX(0);
}

/* Desktop : État avec station - PREMIÈRE apparition (animé) */
.now-playing-wrapper.has-station.first-appearance {
  transition:
    width var(--transition-spring) 200ms,
    transform var(--transition-spring) 200ms,
    opacity 0.4s ease 200ms;
}

/* Mobile : now-playing est position fixed, donc le wrapper doit être transparent */
@media (max-aspect-ratio: 4/3) {
  /* display: contents = le wrapper disparaît du flow, comme s'il n'existait pas */
  .now-playing-wrapper {
    display: contents;
  }

  /* Animation stagger mobile pour now-playing (première apparition) */
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
  padding-top: var(--space-07);
  gap: var(--space-04);
  min-height: 0;
  flex: 1 1 auto;
  flex-shrink: 1;
  touch-action: pan-y;
}

/* :deep(.modal-header) {
  margin: 0 var(--space-06);
} */

/* === SEARCH SECTION === */
.search-section {
  display: flex;
  flex-direction: column;
  margin: 0 var(--space-06);
  gap: var(--space-03);
}

.filters {
  display: flex;
  gap: var(--space-03);
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
  overflow: visible;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.loading-state,
.empty-list,
.error-state {
  display: flex;
  height: 100%;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: var(--space-07);
  gap: var(--space-04);
  color: var(--color-text-secondary);
  text-align: center;
}

.empty-icon,
.error-icon {
  font-size: 64px;
  opacity: 0.5;
}

.empty-icon img {
  width: 64px;
  height: 64px;
  object-fit: cover;
  opacity: 0.5;
}

.stations-grid {
  display: grid;
  /* padding: 0 var(--space-06) var(--space-07) var(--space-06); */
}

/* Mode Recherche */
.stations-grid.search-mode {
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-01);
}

/* Mode Favoris */
.stations-grid.favorites-mode {
  gap: var(--space-03);
  grid-template-columns: repeat(4, minmax(0, 1fr));
}

/* Styles de stations migrés vers StationCard.vue */

.load-more {
  padding-bottom: var(--space-06);
  text-align: center;
}

/* Styles now-playing migrés vers StationCard.vue */



/* Styles loading migrés vers StationCard.vue */

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

/* Appliquer les animations selon l'état de transition */
.stations-grid.transition-fading-out {
  animation: fadeOutUp 300ms ease-out forwards;
}

.stations-grid.transition-fading-in {
  animation: fadeInUp 400ms ease-out forwards;
}






/* Mobile : Responsive adaptations */
@media (max-aspect-ratio: 4/3) {
  .radio-container {
    max-width: none;
    padding-bottom: calc(var(--space-04) + 80px);
    padding-top: var(--space-09);
  }

  .stations-grid {
    /* padding: 0 var(--space-05) 142px var(--space-05); */
  }

  /* :deep(.modal-header) {
    margin: 0 var(--space-05);
  } */

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
