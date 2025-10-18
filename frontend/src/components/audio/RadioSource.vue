<template>
  <div class="radio-overlay">
    <div ref="radioContainer" class="radio-container">
      <div ref="radioContent" class="radio-content" @pointerdown="handlePointerDown" @pointermove="handlePointerMove"
        @pointerup="handlePointerUp" @pointercancel="handlePointerUp">

        <!-- ModalHeader : Vue Favoris -->
        <ModalHeader v-if="!isSearchMode" title="Radios préférées">
          <template #actions>
            <CircularIcon icon="search" variant="dark" @click="openSearch" />
          </template>
        </ModalHeader>

        <!-- ModalHeader : Vue Recherche -->
        <ModalHeader v-else title="Découvrir des radios" :show-back="true" @back="closeSearch">
        </ModalHeader>

        <!-- Recherche et filtres (visible uniquement en mode recherche) -->
        <div v-if="isSearchMode" class="search-section">
          <div class="filters">
            <input v-model="radioStore.searchQuery" type="text" class="filter-input search-input"
              placeholder="Rechercher une station..." @input="handleSearch" />
            <select v-model="radioStore.countryFilter" class="filter-input filter-select" @change="handleSearch">
              <option value="">Tous les pays</option>
              <option value="France">France</option>
              <option value="United Kingdom">Royaume-Uni</option>
              <option value="United States">États-Unis</option>
              <option value="Germany">Allemagne</option>
              <option value="Spain">Espagne</option>
              <option value="Italy">Italie</option>
            </select>

            <select v-model="radioStore.genreFilter" class="filter-input filter-select" @change="handleSearch">
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

          <div v-else class="stations-grid">
            <div v-for="station in displayedStations" :key="station.id" :class="['station-card', {
              active: radioStore.currentStation?.id === station.id,
              playing: radioStore.currentStation?.id === station.id && isCurrentlyPlaying
            }]" @click="playStation(station.id)">
              <div class="station-logo">
                <img v-if="station.favicon" :src="station.favicon" alt="" class="station-favicon"
                  @error="handleStationImageError" />
                <span class="logo-placeholder" :class="{ visible: !station.favicon }">📻</span>
              </div>

              <div class="station-details">
                <h3 class="station-title">{{ station.name }}</h3>
                <p class="station-subtitle">{{ station.country }} • {{ station.genre }}</p>
              </div>

              <button class="favorite-btn" :class="{ active: station.is_favorite }"
                @click.stop="toggleFavorite(station.id)">
                {{ station.is_favorite ? '❤️' : '🤍' }}
              </button>
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

      <!-- Now Playing : Player sticky en bas -->
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

        <button class="control-btn play-btn" @click="togglePlayback">
          {{ isCurrentlyPlaying ? '⏸' : '▶' }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch, nextTick } from 'vue';
import { useRadioStore } from '@/stores/radioStore';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import ModalHeader from '@/components/ui/ModalHeader.vue';
import CircularIcon from '@/components/ui/CircularIcon.vue';

const radioStore = useRadioStore();
const unifiedStore = useUnifiedAudioStore();

const isSearchMode = ref(false);
const displayLimit = ref(20);
const searchDebounceTimer = ref(null);

// Références pour animations
const radioContainer = ref(null);
const radioContent = ref(null);

// État de lecture - TOUJOURS utiliser le store local (source de vérité)
const isCurrentlyPlaying = computed(() => {
  return radioStore.isPlaying;
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
watch(() => unifiedStore.metadata, (newMetadata) => {
  if (unifiedStore.currentSource === 'radio' && newMetadata) {
    radioStore.updateFromWebSocket(newMetadata);
  }
}, { immediate: true, deep: true });

// === LIFECYCLE ===
onMounted(async () => {
  console.log('📻 RadioSource mounted');
  await radioStore.loadStations();
  await radioStore.loadFavorites();
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
  background: rgba(0, 0, 0, 0.3);
  backdrop-filter: blur(32px);
  display: flex;
  align-items: flex-start;
  justify-content: center;
  z-index: 900;
  padding: 48px var(--space-04) var(--space-07) var(--space-04);
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
}

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
  padding-bottom: calc(var(--space-04) + 80px);
  /* Espace pour le player sticky */
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
}

.filter-input {
  flex: 1;
  padding: var(--space-03) var(--space-04);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-04);
  background: var(--color-background);
  color: var(--color-text);
  font-size: var(--font-size-base);
}

.search-input::placeholder {
  color: var(--color-text-light);
}

.filter-select {
  font-size: var(--font-size-sm);
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
  color: var(--color-text-light);
  text-align: center;
}

.empty-icon {
  font-size: 64px;
  opacity: 0.5;
}

.stations-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--space-03);
}

.station-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-03);
  padding: var(--space-04);
  background: var(--color-background);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-04);
  cursor: pointer;
  transition: all 0.2s;
  position: relative;
}

.station-card:hover {
  background: var(--color-background-subtle);
  transform: translateY(-2px);
}

.station-card.active {
  border-color: var(--color-primary);
  background: var(--color-background-subtle);
}

.station-card.playing {
  border-color: var(--color-primary);
  background: var(--color-primary-light);
}

.station-logo {
  width: 100%;
  aspect-ratio: 1;
  border-radius: var(--radius-03);
  overflow: hidden;
  background: var(--color-background-subtle);
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
}

.station-logo .station-favicon {
  width: 100%;
  height: 100%;
  object-fit: cover;
  position: absolute;
  top: 0;
  left: 0;
  z-index: 2;
}

.logo-placeholder {
  font-size: 48px;
  display: none;
  z-index: 1;
}

.logo-placeholder.visible {
  display: flex;
}

.station-details {
  flex: 1;
  width: 100%;
  text-align: center;
}

.station-title {
  margin: 0;
  font-size: var(--font-size-base);
  font-weight: 600;
  color: var(--color-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.station-subtitle {
  margin: var(--space-01) 0 0 0;
  font-size: var(--font-size-sm);
  color: var(--color-text-light);
}

.favorite-btn {
  position: absolute;
  top: var(--space-02);
  right: var(--space-02);
  padding: var(--space-02);
  background: var(--color-background);
  border: none;
  border-radius: var(--radius-full);
  font-size: 20px;
  cursor: pointer;
  transition: transform 0.2s;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
}

.favorite-btn:hover {
  transform: scale(1.2);
}

.load-more {
  padding: var(--space-04);
  text-align: center;
}

.load-more-btn {
  padding: var(--space-03) var(--space-05);
  background: var(--color-background);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-04);
  color: var(--color-text);
  font-size: var(--font-size-sm);
  cursor: pointer;
  transition: all 0.2s;
}

.load-more-btn:hover {
  background: var(--color-background-subtle);
}

/* === NOW PLAYING STICKY PLAYER === */
.now-playing {
  position: fixed;
  bottom: var(--space-04);
  left: 50%;
  transform: translateX(-50%);
  width: calc(100% - var(--space-04) * 2);
  max-width: 728px;
  display: flex;
  align-items: center;
  gap: var(--space-03);
  padding: var(--space-03);
  background: var(--color-background-neutral-50);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-05);
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.2);
  backdrop-filter: blur(16px);
  z-index: 1000;
}

.now-playing .station-art {
  flex-shrink: 0;
  width: 56px;
  height: 56px;
  border-radius: var(--radius-03);
  overflow: hidden;
  background: var(--color-background);
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
}

.now-playing .station-art .current-station-favicon {
  width: 100%;
  height: 100%;
  object-fit: cover;
  position: absolute;
  top: 0;
  left: 0;
  z-index: 2;
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
  font-size: var(--font-size-base);
  font-weight: 600;
  color: var(--color-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.now-playing .station-meta {
  margin: var(--space-01) 0 0 0;
  font-size: var(--font-size-sm);
  color: var(--color-text-light);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.now-playing .control-btn {
  flex-shrink: 0;
  border: none;
  background: var(--color-primary);
  border-radius: var(--radius-full);
  cursor: pointer;
  transition: all 0.2s;
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

/* === RESPONSIVE === */
@media (max-aspect-ratio: 4/3) {
  .radio-overlay {
    padding: 80px var(--space-02) var(--space-02) var(--space-02);
  }

  .radio-container {
    max-width: none;
  }

  .radio-content {
    padding-bottom: calc(var(--space-04) + 88px);
  }

  .stations-grid {
    grid-template-columns: 1fr;
  }

  .filters {
    flex-direction: column;
  }

  .now-playing {
    bottom: var(--space-02);
    width: calc(100% - var(--space-02) * 2);
  }
}
</style>
