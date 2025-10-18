<template>
  <div class="radio-overlay">
    <div ref="radioContainer" class="radio-container">
      <div ref="radioContent" class="radio-content" @pointerdown="handlePointerDown" @pointermove="handlePointerMove"
        @pointerup="handlePointerUp" @pointercancel="handlePointerUp">

        <!-- ModalHeader : Vue Favoris -->
        <ModalHeader v-if="!isSearchMode" title="Radios préférées" variant="neutral">
          <template #actions>
            <CircularIcon icon="search" variant="light" @click="openSearch" />
          </template>
        </ModalHeader>

        <!-- ModalHeader : Vue Recherche -->
        <ModalHeader v-else title="Découvrir des radios" :show-back="true" variant="neutral" @back="closeSearch">
        </ModalHeader>

        <!-- Recherche et filtres (visible uniquement en mode recherche) -->
        <div v-if="isSearchMode" class="search-section">
          <div class="filters">
            <input v-model="radioStore.searchQuery" type="text" class="filter-input search-input text-body-small"
              placeholder="Rechercher..." @input="handleSearch" />
            <select v-model="radioStore.countryFilter" class="filter-input filter-select text-body-small" @change="handleSearch">
              <option value="">Tous les pays</option>
              <option value="France">France</option>
              <option value="United Kingdom">Royaume-Uni</option>
              <option value="United States">États-Unis</option>
              <option value="Germany">Allemagne</option>
              <option value="Spain">Espagne</option>
              <option value="Italy">Italie</option>
            </select>

            <select v-model="radioStore.genreFilter" class="filter-input filter-select text-body-small" @change="handleSearch">
              <option value="">Tous les genres</option>
              <option value="pop">Pop</option>
              <option value="rock">Rock</option>
              <option value="jazz">Jazz</option>
              <option value="classical">Classique</option>
              <option value="electronic">Electronic</option>
              <option value="news">News</option>
            </select>
          </div>
        </div>

        <!-- Liste des stations -->
        <div class="stations-list">
          <div v-if="radioStore.loading" class="loading-state">
            <p>Chargement des stations...</p>
          </div>

          <div v-else-if="displayedStations.length === 0" class="empty-list">
            <div class="empty-icon">📻</div>
            <p class="heading-2">{{ isSearchMode ? 'Aucune station trouvée' : 'Aucune radio favorite' }}</p>
          </div>

          <div v-else class="stations-grid" :class="{ 'favorites-mode': !isSearchMode, 'search-mode': isSearchMode }">
            <!-- Mode Favoris : affichage image seule -->
            <div v-if="!isSearchMode" v-for="station in displayedStations" :key="`fav-${station.id}`" :class="['station-image', {
              active: radioStore.currentStation?.id === station.id,
              playing: radioStore.currentStation?.id === station.id && isCurrentlyPlaying,
              loading: bufferingStationId === station.id
            }]" @click="playStation(station.id)">
              <img v-if="station.favicon" :src="station.favicon" alt="" class="station-img"
                @error="handleStationImageError" />
              <span class="image-placeholder" :class="{ visible: !station.favicon }">📻</span>

              <!-- Loading overlay -->
              <div v-if="bufferingStationId === station.id" class="loading-overlay">
                <div class="loading-spinner"></div>
              </div>
            </div>

            <!-- Mode Recherche : affichage avec informations -->
            <div
              v-else
              v-for="station in displayedStations"
              :key="`search-${station.id}`"
              :class="[
                'station-card',
                {
                  active: radioStore.currentStation?.id === station.id,
                  playing: radioStore.currentStation?.id === station.id && isCurrentlyPlaying,
                  loading: bufferingStationId === station.id
                }
              ]"
              @click="playStation(station.id)"
            >
              <div class="station-logo">
                <img v-if="station.favicon" :src="station.favicon" alt="" class="station-favicon"
                  @error="handleStationImageError" />
                <span class="logo-placeholder" :class="{ visible: !station.favicon }">📻</span>
              </div>

              <div class="station-details">
                <p class="station-title text-body">{{ station.name }}</p>
                <p class="station-subtitle text-mono">{{ station.genre }}</p>
              </div>

              <!-- Loading spinner -->
              <div v-if="bufferingStationId === station.id" class="loading-spinner-small"></div>

              <!-- Stop button -->
              <button
                v-else-if="radioStore.currentStation?.id === station.id && isCurrentlyPlaying"
                class="stop-btn"
                @click.stop="playStation(station.id)"
              >
                ⏸
              </button>
              <!-- <button
                v-else
                class="favorite-btn"
                :class="{ active: station.is_favorite }"
                @click.stop="toggleFavorite(station.id)"
              >
                {{ station.is_favorite ? '❤️' : '🤍' }}
              </button> -->
            </div>
          </div>

          <!-- Bouton "Charger plus" -->
          <div v-if="hasMoreStations" class="load-more">
            <button class="load-more-btn" @click="loadMore">
              Charger plus ({{ remainingStations }} restantes)
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- Now Playing : Desktop - à droite du container, Mobile - sticky en bas -->
    <div v-if="radioStore.currentStation" class="now-playing">
      <div class="station-art">
        <img v-if="radioStore.currentStation.favicon" :src="radioStore.currentStation.favicon" alt="Station logo"
          class="current-station-favicon" @error="handleCurrentStationImageError" />
        <div class="placeholder-logo" :class="{ visible: !radioStore.currentStation.favicon }">📻</div>
      </div>

      <div class="station-info">
        <h3 class="station-name">{{ radioStore.currentStation.name }}</h3>
        <p class="station-meta">{{ radioStore.currentStation.country }} • {{ radioStore.currentStation.genre }}</p>
      </div>

      <button class="favorite-btn" :class="{ active: radioStore.currentStation.is_favorite }"
        @click.stop="toggleFavorite(radioStore.currentStation.id)">
        {{ radioStore.currentStation.is_favorite ? '❤️' : '🤍' }}
      </button>

      <button class="control-btn play-btn" @click="togglePlayback">
        {{ isCurrentlyPlaying ? '⏸' : '▶' }}
      </button>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch, nextTick } from 'vue';
import { useRadioStore } from '@/stores/radioStore';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import useWebSocket from '@/services/websocket';
import ModalHeader from '@/components/ui/ModalHeader.vue';
import CircularIcon from '@/components/ui/CircularIcon.vue';

const radioStore = useRadioStore();
const unifiedStore = useUnifiedAudioStore();
const { on } = useWebSocket();

const isSearchMode = ref(false);
const displayLimit = ref(20);
const searchDebounceTimer = ref(null);

// Références pour animations
const radioContainer = ref(null);
const radioContent = ref(null);

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

// Stations affichées avec limite - favoris ou toutes selon le mode
const displayedStations = computed(() => {
  const stations = isSearchMode.value
    ? radioStore.filteredStations
    : radioStore.favoriteStations;

  return stations.slice(0, displayLimit.value);
});

const hasMoreStations = computed(() => {
  const total = isSearchMode.value
    ? radioStore.filteredStations.length
    : radioStore.favoriteStations.length;

  return total > displayLimit.value;
});

const remainingStations = computed(() => {
  const total = isSearchMode.value
    ? radioStore.filteredStations.length
    : radioStore.favoriteStations.length;

  return total - displayLimit.value;
});

// === ANIMATIONS ===
async function animateIn() {
  await nextTick();

  if (!radioContainer.value || !radioContent.value) return;

  // État initial container
  radioContainer.value.style.transition = 'none';
  radioContainer.value.style.opacity = '0';
  radioContainer.value.style.transform = 'translateY(80px) scale(0.85)';

  // Forcer le reflow
  radioContainer.value.offsetHeight;

  // Animation d'entrée
  setTimeout(() => {
    if (!radioContainer.value) return;
    radioContainer.value.style.transition = 'transform var(--transition-spring), opacity 400ms ease-out';
    radioContainer.value.style.opacity = '1';
    radioContainer.value.style.transform = 'translateY(0) scale(1)';
  }, 100);
}

// === NAVIGATION ===
function openSearch() {
  isSearchMode.value = true;
  displayLimit.value = 20;
  radioStore.loadStations(false); // Charger toutes les stations
}

function closeSearch() {
  isSearchMode.value = false;
  displayLimit.value = 20;
  // Retour au mode favoris - réinitialiser les filtres
  radioStore.searchQuery = '';
  radioStore.countryFilter = '';
  radioStore.genreFilter = '';
  radioStore.loadStations(true); // Charger les favoris
}

// === ACTIONS ===
function loadMore() {
  displayLimit.value += 20;
}

function handleSearch() {
  // Debounce pour éviter trop d'appels API
  if (searchDebounceTimer.value) {
    clearTimeout(searchDebounceTimer.value);
  }

  searchDebounceTimer.value = setTimeout(async () => {
    displayLimit.value = 20;
    await radioStore.loadStations(false);
  }, 300);
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

function handleCurrentStationImageError(e) {
  e.target.style.display = 'none';
  const placeholder = e.target.nextElementSibling;
  if (placeholder) {
    placeholder.classList.add('visible');
  }
}

function handleStationImageError(e) {
  e.target.style.display = 'none';
  const placeholder = e.target.nextElementSibling;
  if (placeholder) {
    placeholder.classList.add('visible');
  }
}

// === POINTER SCROLL ===
let isDragging = false;
let startY = 0;
let startScrollTop = 0;
let pointerId = null;

function handlePointerDown(event) {
  if (!radioContent.value) return;

  const isSlider = event.target.closest('input[type="range"]');
  const isButton = event.target.closest('button');
  const isInput = event.target.closest('input, select, textarea');

  if (isSlider || isButton || isInput) {
    return;
  }

  isDragging = true;
  pointerId = event.pointerId;
  startY = event.clientY;
  startScrollTop = radioContent.value.scrollTop;
}

function handlePointerMove(event) {
  if (!isDragging || event.pointerId !== pointerId || !radioContent.value) return;

  const deltaY = Math.abs(startY - event.clientY);

  if (deltaY > 5) {
    if (!radioContent.value.hasPointerCapture(event.pointerId)) {
      radioContent.value.setPointerCapture(event.pointerId);
    }

    event.preventDefault();

    const scrollDelta = startY - event.clientY;
    radioContent.value.scrollTop = startScrollTop + scrollDelta;
  }
}

function handlePointerUp(event) {
  if (event.pointerId === pointerId) {
    isDragging = false;
    pointerId = null;

    if (radioContent.value && radioContent.value.hasPointerCapture(event.pointerId)) {
      radioContent.value.releasePointerCapture(event.pointerId);
    }
  }
}

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

  animateIn();
});
</script>

<style scoped>
::-webkit-scrollbar {
  display: none;
}

/* === LAYOUT === */
.radio-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: var(--color-background-radio);
  display: flex;
  align-items: flex-start;
  justify-content: center;
  z-index: 900;
  padding: var(--space-07);
  gap: var(--space-04);
}

.radio-container {
  position: relative;
  background: var(--color-background-neutral-50);
  border-radius: var(--radius-07);
  width: 100%;
  max-width: 768px;
  max-height: 100%;
  display: flex;
  flex-direction: column;
  opacity: 0;
  transition: max-width var(--transition-normal);
}

/* Desktop : réduire la largeur du container quand now-playing est visible */
/* @media (min-aspect-ratio: 4/3) {
  .radio-container {
    max-width: calc(768px - 320px - var(--space-04));
  }
} */

.radio-container::before {
  content: '';
  position: absolute;
  inset: 0;
  padding: 2px;
  opacity: 0.8;
  background: var(--stroke-glass);
  border-radius: var(--radius-07);
  -webkit-mask:
    linear-gradient(#000 0 0) content-box,
    linear-gradient(#000 0 0);
  -webkit-mask-composite: xor;
  mask-composite: exclude;
  z-index: -1;
  pointer-events: none;
}

.radio-content {
  overflow-y: auto;
  padding: var(--space-04);
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
  min-height: 0;
  flex: 1;
  border-radius: var(--radius-07);
  touch-action: pan-y;
}

/* Mobile : espace pour le player sticky en bas */
@media (max-aspect-ratio: 4/3) {
  .radio-content {
    padding-bottom: calc(var(--space-04) + 80px);
  }
}

/* === SEARCH SECTION === */
.search-section {
  display: flex;
  flex-direction: column;
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
  gap: var(--space-04);
}

.loading-state,
.empty-list {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: var(--space-07);
  gap: var(--space-04);
  color: var(--color-text-secondary);
  text-align: center;
}

.empty-icon {
  font-size: 64px;
  opacity: 0.5;
}

.stations-grid {
  display: grid;
  gap: var(--space-01);
}

/* Mode Recherche : 2 colonnes */
.stations-grid.search-mode {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

/* Mode Favoris : 3 colonnes */
.stations-grid.favorites-mode {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

/* === STATION IMAGE (Mode Favoris - Image seule) === */
.station-image {
  aspect-ratio: 1 / 1;
  width: 100%;
  border-radius: var(--radius-05);
  overflow: hidden;
  cursor: pointer;
  transition: transform var(--transition-fast), box-shadow var(--transition-fast);
  position: relative;
  background: var(--color-background-neutral);
  border: 2px solid var(--color-border);
  display: flex;
  align-items: center;
  justify-content: center;
}

.station-image:hover {
  transform: scale(1.05);
  box-shadow: var(--shadow-02);
}

.station-image.active {
  border-color: var(--color-brand);
}

.station-image.playing {
  border-color: var(--color-brand);
  box-shadow: 0 0 0 4px rgba(239, 100, 46, 0.2);
}

.station-image .station-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: center;
  position: absolute;
  top: 0;
  left: 0;
  z-index: 1;
  display: block;
}

.station-image .image-placeholder {
  font-size: 48px;
  display: none;
  z-index: 0;
}

.station-image .image-placeholder.visible {
  display: flex;
}

/* === STATION CARD (Mode Recherche - Avec informations) === */
.station-card {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: var(--space-02);
  padding: var(--space-02);
  background: var(--color-background-neutral);
  border: 2px solid var(--color-border);
  border-radius: var(--radius-04);
  cursor: pointer;
  transition: all var(--transition-fast);
  position: relative;
}

.station-card:hover {
  background: var(--color-background);
  transform: translateY(-2px);
  box-shadow: var(--shadow-02);
}

.station-card.active {
  border-color: var(--color-brand);
}

.station-card.playing {
  border-color: var(--color-brand);
  background: var(--color-background);
  box-shadow: 0 0 0 4px rgba(239, 100, 46, 0.2);
}

.station-logo {
  flex-shrink: 0;
  width: 52px;
  height: 52px;
  position: relative;
  border-radius: var(--radius-02);
  overflow: hidden;
  background: var(--color-background);
}

.station-logo .station-favicon {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: center;
  display: block;
}

.logo-placeholder {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  font-size: 32px;
  display: none;
}

.logo-placeholder.visible {
  display: flex;
}

.station-details {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: var(--space-01);
}

.station-title {
  margin: 0;
  font-size: var(--font-size-body-small);
  font-weight: 500;
  color: var(--color-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.station-subtitle {
  margin: 0;
  font-size: var(--font-size-small);
  color: var(--color-text-light);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.station-card .favorite-btn,
.station-card .stop-btn {
  flex-shrink: 0;
  width: 40px;
  height: 40px;
  padding: 0;
  background: var(--color-background-neutral);
  border: none;
  border-radius: var(--radius-full);
  font-size: 20px;
  cursor: pointer;
  transition: transform var(--transition-fast);
  box-shadow: var(--shadow-02);
  display: flex;
  align-items: center;
  justify-content: center;
}

.station-card .stop-btn {
  background: var(--color-brand);
  font-size: 18px;
}

.station-card .favorite-btn:hover,
.station-card .stop-btn:hover {
  transform: scale(1.1);
}

.station-card .favorite-btn:active,
.station-card .stop-btn:active {
  transform: scale(0.95);
}

.load-more {
  padding: var(--space-04);
  text-align: center;
}

.load-more-btn {
  padding: var(--space-03) var(--space-05);
  background: var(--color-background-neutral);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-04);
  color: var(--color-text);
  font-size: var(--font-size-body);
  cursor: pointer;
  transition: all var(--transition-fast);
}

.load-more-btn:hover {
  background: var(--color-background);
  transform: translateY(-2px);
}

/* === NOW PLAYING === */

/* Desktop : Panel à droite avec image en haut */
@media (min-aspect-ratio: 4/3) {
  .now-playing {
    position: relative;
    width: 310px;
    flex-shrink: 0;
    display: flex;
    flex-direction: column;
    gap: var(--space-04);
    padding: var(--space-04);
    background: var(--color-background-neutral-50);
    border-radius: var(--radius-07);
    box-shadow: 0 var(--space-04) var(--space-07) rgba(0, 0, 0, 0.2);
    backdrop-filter: blur(16px);
  }

  .now-playing::before {
    content: '';
    position: absolute;
    inset: 0;
    padding: 2px;
    opacity: 0.8;
    background: var(--stroke-glass);
    border-radius: var(--radius-07);
    -webkit-mask:
      linear-gradient(#000 0 0) content-box,
      linear-gradient(#000 0 0);
    -webkit-mask-composite: xor;
    mask-composite: exclude;
    z-index: -1;
    pointer-events: none;
  }

  .now-playing .station-art {
    width: 100%;
    aspect-ratio: 1 / 1;
    border-radius: var(--radius-05);
    overflow: hidden;
    background: var(--color-background-neutral);
    display: flex;
    align-items: center;
    justify-content: center;
    position: relative;
    flex-shrink: 0;
  }

  .now-playing .station-art .current-station-favicon {
    width: 100%;
    height: 100%;
    object-fit: cover;
    object-position: center;
    position: absolute;
    top: 0;
    left: 0;
    z-index: 2;
    display: block;
  }

  .now-playing .placeholder-logo {
    font-size: 64px;
    display: none;
    z-index: 1;
  }

  .now-playing .placeholder-logo.visible {
    display: flex;
  }

  .now-playing .station-info {
    display: flex;
    flex-direction: column;
    gap: var(--space-02);
    text-align: center;
  }

  .now-playing .station-name {
    margin: 0;
    font-size: var(--font-size-h2);
    font-weight: 500;
    color: var(--color-text);
    overflow: hidden;
    text-overflow: ellipsis;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
  }

  .now-playing .station-meta {
    margin: 0;
    font-size: var(--font-size-body);
    color: var(--color-text-secondary);
  }

  .now-playing .favorite-btn {
    width: 56px;
    height: 56px;
    border: none;
    background: var(--color-background-neutral);
    border-radius: var(--radius-full);
    font-size: 24px;
    cursor: pointer;
    transition: transform var(--transition-fast);
    box-shadow: var(--shadow-02);
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .now-playing .favorite-btn:hover {
    transform: scale(1.1);
  }

  .now-playing .favorite-btn:active {
    transform: scale(0.95);
  }

  .now-playing .control-btn {
    width: 100%;
    height: 56px;
    border: none;
    background: var(--color-brand);
    border-radius: var(--radius-05);
    cursor: pointer;
    transition: all var(--transition-fast);
    font-size: 24px;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .now-playing .control-btn:hover {
    transform: scale(1.02);
  }

  .now-playing .control-btn:active {
    transform: scale(0.98);
  }
}

/* Mobile : Player sticky en bas (layout horizontal) */
@media (max-aspect-ratio: 4/3) {
  .now-playing {
    position: fixed;
    bottom: var(--space-02);
    left: 50%;
    transform: translateX(-50%);
    width: calc(100% - var(--space-02) * 2);
    display: flex;
    flex-direction: row;
    align-items: center;
    gap: var(--space-03);
    padding: var(--space-03);
    background: var(--color-background-neutral-50);
    border: 1px solid var(--color-border);
    border-radius: var(--radius-05);
    box-shadow: 0 var(--space-04) var(--space-07) rgba(0, 0, 0, 0.2);
    backdrop-filter: blur(16px);
    z-index: 1000;
  }

  .now-playing .station-art {
    flex-shrink: 0;
    width: 56px;
    height: 56px;
    aspect-ratio: 1 / 1;
    border-radius: var(--radius-03);
    overflow: hidden;
    background: var(--color-background-neutral);
    display: flex;
    align-items: center;
    justify-content: center;
    position: relative;
  }

  .now-playing .station-art .current-station-favicon {
    width: 100%;
    height: 100%;
    object-fit: cover;
    object-position: center;
    position: absolute;
    top: 0;
    left: 0;
    z-index: 2;
    display: block;
  }

  .now-playing .placeholder-logo {
    font-size: 32px;
    display: none;
    z-index: 1;
  }

  .now-playing .placeholder-logo.visible {
    display: flex;
  }

  .now-playing .station-info {
    flex: 1;
    min-width: 0;
  }

  .now-playing .station-name {
    margin: 0;
    font-size: var(--font-size-body);
    font-weight: 500;
    color: var(--color-text);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .now-playing .station-meta {
    margin: var(--space-01) 0 0 0;
    font-size: var(--font-size-body);
    color: var(--color-text-secondary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .now-playing .favorite-btn {
    flex-shrink: 0;
    width: 48px;
    height: 48px;
    border: none;
    background: var(--color-background-neutral);
    border-radius: var(--radius-full);
    font-size: 20px;
    cursor: pointer;
    transition: transform var(--transition-fast);
    box-shadow: var(--shadow-02);
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .now-playing .favorite-btn:hover {
    transform: scale(1.1);
  }

  .now-playing .favorite-btn:active {
    transform: scale(0.95);
  }

  .now-playing .control-btn {
    flex-shrink: 0;
    border: none;
    background: var(--color-brand);
    border-radius: var(--radius-full);
    cursor: pointer;
    transition: all var(--transition-fast);
    font-size: 24px;
    width: 56px;
    height: 56px;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .now-playing .control-btn:hover {
    transform: scale(1.05);
  }

  .now-playing .control-btn:active {
    transform: scale(0.95);
  }
}

/* === LOADING STATES === */

/* Loading overlay pour les stations en mode favoris */
.loading-overlay {
  position: absolute;
  inset: 0;
  background: rgba(0, 0, 0, 0.6);
  backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-05);
  z-index: 10;
}

/* Spinner pour mode favoris (overlay) */
.loading-spinner {
  width: 40px;
  height: 40px;
  border: 3px solid rgba(255, 255, 255, 0.2);
  border-top-color: var(--color-brand);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

/* Spinner pour mode recherche (petit, à la place du bouton) */
.loading-spinner-small {
  flex-shrink: 0;
  width: 40px;
  height: 40px;
  border: 3px solid var(--color-border);
  border-top-color: var(--color-brand);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

/* Réduire légèrement l'opacité des stations en loading */
.station-image.loading,
.station-card.loading {
  opacity: 0.9;
}

/* === RESPONSIVE === */
@media (max-aspect-ratio: 4/3) {
  .radio-overlay {
    padding: 80px var(--space-02) var(--space-02) var(--space-02);
  }

  .radio-container {
    max-width: none;
  }

  /* Mode Favoris : rester en grille 3 colonnes sur mobile */
  .stations-grid.favorites-mode {
    grid-template-columns: repeat(3, 1fr);
  }

  /* Mode Recherche : 1 colonne sur mobile */
  .stations-grid.search-mode {
    grid-template-columns: 1fr;
  }

  .filters {
    flex-direction: column;
  }
}
</style>
