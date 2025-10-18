<template>
  <div class="radio-player">
    <!-- Section lecture en cours -->
    <div v-if="radioStore.currentStation" class="now-playing stagger-1">
      <div class="station-art-section">
        <div class="station-art-container">
          <!-- Station logo ou placeholder -->
          <div class="station-art">
            <img v-if="radioStore.currentStation.favicon"
                 :src="radioStore.currentStation.favicon"
                 alt="Station logo"
                 class="current-station-favicon"
                 @error="handleCurrentStationImageError" />
            <div class="placeholder-logo" :class="{ visible: !radioStore.currentStation.favicon }">📻</div>
          </div>
        </div>
      </div>

      <div class="content-section">
        <div class="station-info stagger-2">
          <h1 class="station-name heading-1">{{ radioStore.currentStation.name }}</h1>
          <p class="station-meta heading-2">
            {{ radioStore.currentStation.country }} • {{ radioStore.currentStation.genre }}
          </p>
        </div>

        <div class="controls-section stagger-3">
          <div class="radio-controls">
            <button class="control-btn play-btn" @click="togglePlayback">
              {{ isCurrentlyPlaying ? '⏸' : '▶' }}
            </button>
            <button class="control-btn fav-btn" @click="toggleFavorite(radioStore.currentStation.id)">
              {{ radioStore.currentStation.is_favorite ? '❤️' : '🤍' }}
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- Message si aucune station en lecture -->
    <div v-else class="empty-state stagger-1">
      <div class="empty-icon">📻</div>
      <p class="heading-2">Sélectionnez une station pour commencer</p>
    </div>

    <!-- Recherche et filtres -->
    <div class="search-section stagger-2">
      <input
        v-model="radioStore.searchQuery"
        type="text"
        class="search-input"
        placeholder="Rechercher une station..."
        @input="handleSearch"
      />

      <div class="filters">
        <select v-model="radioStore.countryFilter" class="filter-select" @change="handleSearch">
          <option value="">Tous les pays</option>
          <option value="France">France</option>
          <option value="United Kingdom">Royaume-Uni</option>
          <option value="United States">États-Unis</option>
          <option value="Germany">Allemagne</option>
          <option value="Spain">Espagne</option>
          <option value="Italy">Italie</option>
        </select>

        <select v-model="radioStore.genreFilter" class="filter-select" @change="handleSearch">
          <option value="">Tous les genres</option>
          <option value="pop">Pop</option>
          <option value="rock">Rock</option>
          <option value="jazz">Jazz</option>
          <option value="classical">Classique</option>
          <option value="electronic">Electronic</option>
          <option value="news">News</option>
        </select>
      </div>

      <div class="view-tabs">
        <button
          :class="['tab-button', { active: activeTab === 'all' }]"
          @click="activeTab = 'all'; loadStations()"
        >
          Toutes
        </button>
        <button
          :class="['tab-button', { active: activeTab === 'favorites' }]"
          @click="activeTab = 'favorites'; loadStations()"
        >
          Favoris
        </button>
      </div>
    </div>

    <!-- Liste des stations -->
    <div class="stations-list stagger-3">
      <div v-if="radioStore.loading" class="loading-state">
        <p>Chargement des stations...</p>
      </div>

      <div v-else-if="displayedStations.length === 0" class="empty-list">
        <p>Aucune station trouvée</p>
      </div>

      <div v-else class="stations-grid">
        <div
          v-for="station in displayedStations"
          :key="station.id"
          :class="['station-card', {
            active: radioStore.currentStation?.id === station.id,
            playing: radioStore.currentStation?.id === station.id && isCurrentlyPlaying
          }]"
          @click="playStation(station.id)"
        >
          <div class="station-logo">
            <img v-if="station.favicon"
                 :src="station.favicon"
                 alt=""
                 class="station-favicon"
                 @error="handleStationImageError" />
            <span class="logo-placeholder" :class="{ visible: !station.favicon }">📻</span>
          </div>

          <div class="station-details">
            <h3 class="station-title">{{ station.name }}</h3>
            <p class="station-subtitle">{{ station.country }} • {{ station.genre }}</p>
          </div>

          <button
            class="favorite-btn"
            :class="{ active: station.is_favorite }"
            @click.stop="toggleFavorite(station.id)"
          >
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
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue';
import { useRadioStore } from '@/stores/radioStore';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';

const radioStore = useRadioStore();
const unifiedStore = useUnifiedAudioStore();

const activeTab = ref('all');
const displayLimit = ref(20);
const searchDebounceTimer = ref(null);

// État de lecture - TOUJOURS utiliser le store local (source de vérité)
// Le WebSocket est ignoré pour éviter les conflits avec les actions utilisateur
const isCurrentlyPlaying = computed(() => {
  return radioStore.isPlaying;
});

// Stations affichées avec limite
const displayedStations = computed(() => {
  const stations = activeTab.value === 'favorites'
    ? radioStore.favoriteStations
    : radioStore.filteredStations;

  return stations.slice(0, displayLimit.value);
});

const hasMoreStations = computed(() => {
  const total = activeTab.value === 'favorites'
    ? radioStore.favoriteStations.length
    : radioStore.filteredStations.length;

  return total > displayLimit.value;
});

const remainingStations = computed(() => {
  const total = activeTab.value === 'favorites'
    ? radioStore.favoriteStations.length
    : radioStore.filteredStations.length;

  return total - displayLimit.value;
});

// Actions
async function loadStations() {
  displayLimit.value = 20; // Reset limit
  await radioStore.loadStations(activeTab.value === 'favorites');
}

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
    await radioStore.loadStations(activeTab.value === 'favorites');
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
  console.log('🎵 togglePlayback - isPlaying:', isCurrentlyPlaying.value, 'currentStation:', radioStore.currentStation);

  if (isCurrentlyPlaying.value) {
    // Arrêter la lecture
    console.log('🛑 Stopping playback...');
    await radioStore.stopPlayback();
  } else if (radioStore.currentStation) {
    // Relancer la station (même si on vient de l'arrêter)
    // Le endpoint /play gérera le démarrage du plugin si nécessaire
    console.log('▶️ Starting playback for station:', radioStore.currentStation.id);
    await radioStore.playStation(radioStore.currentStation.id);
  } else {
    console.warn('⚠️ No current station to play');
  }
}

async function toggleFavorite(stationId) {
  await radioStore.toggleFavorite(stationId);
}

function handleCurrentStationImageError(e) {
  // Cache l'image de la station en cours et affiche le placeholder
  e.target.style.display = 'none';
  const placeholder = e.target.nextElementSibling;
  if (placeholder) {
    placeholder.classList.add('visible');
  }
}

function handleStationImageError(e) {
  // Cache l'image de la station et affiche le placeholder
  e.target.style.display = 'none';
  const placeholder = e.target.nextElementSibling;
  if (placeholder) {
    placeholder.classList.add('visible');
  }
}

// Lifecycle
onMounted(async () => {
  console.log('📻 RadioSource mounted');
  await radioStore.loadStations();
  await radioStore.loadFavorites();
});
</script>

<style scoped>
/* Staggering animations */
.stagger-1, .stagger-2, .stagger-3 {
  opacity: 0;
  transform: translateY(var(--space-07));
}

.radio-player .stagger-1,
.radio-player .stagger-2,
.radio-player .stagger-3 {
  animation:
    stagger-transform var(--transition-spring) forwards,
    stagger-opacity 0.4s ease forwards;
}

.radio-player .stagger-1 { animation-delay: 0ms; }
.radio-player .stagger-2 { animation-delay: 100ms; }
.radio-player .stagger-3 { animation-delay: 200ms; }

@keyframes stagger-transform {
  to { transform: translateY(0); }
}

@keyframes stagger-opacity {
  to { opacity: 1; }
}

/* Layout */
.radio-player {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  background: var(--color-background-neutral);
  padding: var(--space-05);
  gap: var(--space-05);
  overflow: hidden;
}

/* Now playing section */
.now-playing {
  display: flex;
  gap: var(--space-05);
  padding: var(--space-04);
  background: var(--color-background-subtle);
  border-radius: var(--radius-05);
}

.station-art-section {
  flex-shrink: 0;
  width: 120px;
  height: 120px;
}

.station-art-container {
  position: relative;
  width: 100%;
  height: 100%;
}

.station-art {
  width: 100%;
  height: 100%;
  border-radius: var(--radius-04);
  overflow: hidden;
  background: var(--color-background);
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
}

.station-art .current-station-favicon {
  width: 100%;
  height: 100%;
  object-fit: cover;
  position: absolute;
  top: 0;
  left: 0;
  z-index: 2;
}

.placeholder-logo {
  font-size: 48px;
  display: none;
  z-index: 1;
}

.placeholder-logo.visible {
  display: flex;
}

.content-section {
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  min-width: 0;
}

.station-info {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.station-name {
  color: var(--color-text);
  margin: 0;
}

.station-meta {
  color: var(--color-text-light);
  margin: 0;
}

.controls-section {
  margin-top: var(--space-03);
}

.radio-controls {
  display: flex;
  gap: var(--space-03);
  align-items: center;
}

.control-btn {
  border: none;
  background: var(--color-background);
  border-radius: var(--radius-05);
  cursor: pointer;
  transition: all 0.2s;
  font-size: 32px;
  width: 64px;
  height: 64px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.control-btn:hover {
  transform: scale(1.05);
}

.control-btn:active {
  transform: scale(0.95);
}

.play-btn {
  background: var(--color-primary);
}

.fav-btn {
  background: var(--color-background-subtle);
}

/* Empty state */
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: var(--space-07);
  gap: var(--space-04);
  text-align: center;
}

.empty-icon {
  font-size: 64px;
  opacity: 0.5;
}

/* Search section */
.search-section {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

.search-input {
  width: 100%;
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

.filters {
  display: flex;
  gap: var(--space-03);
}

.filter-select {
  flex: 1;
  padding: var(--space-03);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-04);
  background: var(--color-background);
  color: var(--color-text);
  font-size: var(--font-size-sm);
}

.view-tabs {
  display: flex;
  gap: var(--space-02);
}

.tab-button {
  flex: 1;
  padding: var(--space-03);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-04);
  background: var(--color-background);
  color: var(--color-text-light);
  font-size: var(--font-size-sm);
  cursor: pointer;
  transition: all 0.2s;
}

.tab-button.active {
  background: var(--color-primary);
  border-color: var(--color-primary);
}

/* Stations list */
.stations-list {
  flex: 1;
  overflow-y: auto;
  min-height: 0;
}

.loading-state,
.empty-list {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-07);
  color: var(--color-text-light);
}

.stations-grid {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.station-card {
  display: flex;
  align-items: center;
  gap: var(--space-03);
  padding: var(--space-03);
  background: var(--color-background);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-04);
  cursor: pointer;
  transition: all 0.2s;
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
  flex-shrink: 0;
  width: 48px;
  height: 48px;
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
  font-size: 24px;
  display: none;
  z-index: 1;
}

.logo-placeholder.visible {
  display: flex;
}

.station-details {
  flex: 1;
  min-width: 0;
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
  flex-shrink: 0;
  padding: var(--space-02);
  background: transparent;
  border: none;
  font-size: 20px;
  cursor: pointer;
  transition: transform 0.2s;
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

@media (max-aspect-ratio: 4/3) {
  .now-playing {
    flex-direction: column;
  }

  .station-art-section {
    width: 100%;
    height: auto;
    aspect-ratio: 1;
  }
}
</style>
