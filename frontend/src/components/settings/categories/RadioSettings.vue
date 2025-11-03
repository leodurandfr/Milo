<template>
  <section class="settings-section">
    <!-- Ajouter une station -->
    <div class="radio-group">
      <h2 class="heading-2 text-body">Stations personnalisées</h2>
      <div class="setting-item-container">
        <div class="radio-description text-mono">
          Ajoutez vos propres stations radio avec leur image
        </div>
        <div class="action-buttons">
          <Button variant="brand" @click="$emit('go-to-add-station')">
            + Ajouter une station
          </Button>
          <Button variant="neutral" @click="$emit('go-to-change-image')">
            🖼️ Changer une image
          </Button>
        </div>
      </div>
    </div>

    <!-- Stations existantes avec image modifiée -->
    <div v-if="existingStationsWithModifiedImage.length > 0" class="radio-group">
      <h2 class="heading-2 text-body">🖼️ Images modifiées ({{ existingStationsWithModifiedImage.length }})</h2>
      <div class="radio-description text-mono">
        Radios existantes avec une image importée manuellement
      </div>
      <div class="stations-list">
        <div v-for="station in existingStationsWithModifiedImage" :key="station.id" class="station-item modified">
          <div class="station-image">
            <img v-if="station.favicon" :src="station.favicon" :alt="station.name" @error="handleImageError" />
            <img v-else :src="placeholderImg" alt="Station sans image" class="no-image" />
          </div>
          <div class="station-info">
            <div class="station-name text-body">{{ station.name }}</div>
            <div class="station-details text-mono">
              {{ station.country }} • {{ station.genre }}
            </div>
          </div>
          <div class="station-actions">
            <button class="icon-btn remove-image-btn" @click="confirmRemoveImage(station)" title="Restaurer l'image d'origine">
              ❌
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- Stations personnalisées avec image -->
    <div v-if="customStationsWithImage.length > 0" class="radio-group">
      <h2 class="heading-2 text-body">⭐ Stations personnalisées avec image ({{ customStationsWithImage.length }})</h2>
      <div class="radio-description text-mono">
        Stations ajoutées manuellement avec une image
      </div>
      <div class="stations-list">
        <div v-for="station in customStationsWithImage" :key="station.id" class="station-item custom">
          <div class="station-image">
            <img v-if="station.favicon" :src="station.favicon" :alt="station.name" @error="handleImageError" />
            <img v-else :src="placeholderImg" alt="Station sans image" class="no-image" />
          </div>
          <div class="station-info">
            <div class="station-name text-body">{{ station.name }}</div>
            <div class="station-details text-mono">
              {{ station.country }} • {{ station.genre }}
            </div>
          </div>
          <div class="station-actions">
            <button class="icon-btn remove-image-btn" @click="confirmRemoveImage(station)" title="Supprimer l'image">
              ❌
            </button>
            <button class="icon-btn delete-btn" @click="confirmDelete(station)" title="Supprimer la station">
              🗑️
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- Stations personnalisées sans image -->
    <div v-if="customStationsWithoutImage.length > 0" class="radio-group">
      <h2 class="heading-2 text-body">Stations personnalisées sans image ({{ customStationsWithoutImage.length }})</h2>
      <div class="radio-description text-mono">
        Stations ajoutées manuellement sans image
      </div>
      <div class="stations-list">
        <div v-for="station in customStationsWithoutImage" :key="station.id" class="station-item">
          <div class="station-image">
            <img :src="placeholderImg" alt="Station sans image" class="no-image" />
          </div>
          <div class="station-info">
            <div class="station-name text-body">{{ station.name }}</div>
            <div class="station-details text-mono">
              {{ station.country }} • {{ station.genre }}
            </div>
            <div class="station-url text-mono">{{ station.url }}</div>
          </div>
          <div class="station-actions">
            <button class="icon-btn delete-btn" @click="confirmDelete(station)" title="Supprimer la station">
              🗑️
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- Aucune station -->
    <div v-if="customStations.length === 0" class="radio-group">
      <div class="empty-state text-mono">
        Aucune station personnalisée. Cliquez sur "Ajouter une station" pour commencer.
      </div>
    </div>

    <!-- Delete Confirmation Modal -->
    <div v-if="stationToDelete" class="modal-overlay" @click.self="stationToDelete = null">
      <div class="confirm-modal">
        <h3 class="text-body">Supprimer la station ?</h3>
        <p class="text-mono">{{ stationToDelete.name }}</p>
        <div class="confirm-actions">
          <button class="btn-secondary" @click="stationToDelete = null">Annuler</button>
          <button class="btn-danger" @click="deleteStation">Supprimer</button>
        </div>
      </div>
    </div>

    <!-- Remove Image Confirmation Modal -->
    <div v-if="stationToRemoveImage" class="modal-overlay" @click.self="stationToRemoveImage = null">
      <div class="confirm-modal">
        <h3 class="text-body">Supprimer l'image importée ?</h3>
        <p class="text-mono">{{ stationToRemoveImage.name }}</p>
        <p class="text-mono" style="color: var(--color-text-secondary); font-size: var(--font-size-small);">
          L'image sera supprimée et la station reviendra à son image d'origine.
        </p>
        <div class="confirm-actions">
          <button class="btn-secondary" @click="stationToRemoveImage = null">Annuler</button>
          <button class="btn-danger" @click="removeImage">Supprimer l'image</button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue';
import axios from 'axios';
import { useRadioStore } from '@/stores/radioStore';
import Button from '@/components/ui/Button.vue';
import placeholderImg from '@/assets/radio/station-placeholder.jpg';

defineEmits(['go-to-add-station', 'go-to-change-image']);

const radioStore = useRadioStore();
const stationToDelete = ref(null);
const stationToRemoveImage = ref(null);

// Listes locales chargées depuis l'API
const customStations = ref([]);
const favoriteStations = ref([]);

// Stations favorites avec image modifiée (non-custom)
const existingStationsWithModifiedImage = computed(() => {
  return favoriteStations.value.filter(s =>
    s.image_filename && !s.id.startsWith('custom_')
  );
});

// Stations personnalisées avec image
const customStationsWithImage = computed(() => {
  return customStations.value.filter(s => s.image_filename);
});

// Stations personnalisées sans image uploadée
const customStationsWithoutImage = computed(() => {
  return customStations.value.filter(s => !s.image_filename);
});

async function loadCustomStations() {
  // Charger les stations personnalisées
  try {
    const customResponse = await axios.get('/api/radio/custom');
    customStations.value = customResponse.data;
  } catch (error) {
    console.error('Erreur chargement stations personnalisées:', error);
    customStations.value = [];
  }

  // Charger les stations favorites pour voir celles avec images modifiées
  try {
    const favoritesResponse = await axios.get('/api/radio/stations', {
      params: { favorites_only: true, limit: 10000 }
    });
    favoriteStations.value = favoritesResponse.data.stations;
  } catch (error) {
    console.error('Erreur chargement favoris:', error);
    favoriteStations.value = [];
  }
}

// Exposer loadCustomStations pour que SettingsModal puisse recharger les données
defineExpose({ loadCustomStations });

function confirmDelete(station) {
  stationToDelete.value = station;
}

async function deleteStation() {
  if (!stationToDelete.value) return;

  const success = await radioStore.removeCustomStation(stationToDelete.value.id);

  if (success) {
    console.log('✅ Station supprimée');
    await loadCustomStations();
  } else {
    console.error('❌ Échec suppression station');
  }

  stationToDelete.value = null;
}

function confirmRemoveImage(station) {
  stationToRemoveImage.value = station;
}

async function removeImage() {
  if (!stationToRemoveImage.value) return;

  const result = await radioStore.removeStationImage(stationToRemoveImage.value.id);

  if (result.success) {
    console.log('✅ Image supprimée');
    loadCustomStations();
  } else {
    console.error('❌ Échec suppression image:', result.error);
  }

  stationToRemoveImage.value = null;
}

function handleImageError(event) {
  event.target.style.display = 'none';
  const parent = event.target.parentElement;
  if (parent) {
    const img = document.createElement('img');
    img.src = placeholderImg;
    img.alt = 'Station sans image';
    img.className = 'no-image';
    parent.innerHTML = '';
    parent.appendChild(img);
  }
}

onMounted(() => {
  loadCustomStations();
});
</script>

<style scoped>
.settings-section {
  background: var(--color-background-neutral);
  border-radius: var(--radius-04);
  padding: var(--space-05-fixed) var(--space-05);
  display: flex;
  flex-direction: column;
  gap: var(--space-05-fixed);
}

.radio-group {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

.setting-item-container {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

.radio-description {
  color: var(--color-text-secondary);
}

.action-buttons {
  display: flex;
  gap: var(--space-03);
  flex-wrap: wrap;
}

/* Stations List */
.stations-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

.station-item {
  display: flex;
  align-items: center;
  gap: var(--space-04);
  padding: var(--space-04);
  background: var(--color-background);
  border-radius: var(--radius-04);
  border: 1px solid var(--color-border);
  transition: transform var(--transition-fast);
}

.station-item:hover {
  transform: translateY(-2px);
}

.station-item.modified {
  border-color: var(--color-brand);
  background: linear-gradient(135deg, var(--color-background) 0%, rgba(255, 255, 255, 0.02) 100%);
}

.station-item.custom {
  border-color: #ffa500;
  background: linear-gradient(135deg, var(--color-background) 0%, rgba(255, 165, 0, 0.05) 100%);
}

.station-image {
  width: 60px;
  height: 60px;
  min-width: 60px;
  border-radius: var(--radius-04);
  overflow: hidden;
  background: var(--color-background-neutral);
  display: flex;
  align-items: center;
  justify-content: center;
}

.station-image img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.no-image {
  font-size: 32px;
  color: var(--color-text-secondary);
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.station-info {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
  min-width: 0;
}

.station-name {
  font-weight: 600;
  color: var(--color-text);
}

.station-details {
  color: var(--color-text-secondary);
  font-size: var(--font-size-small);
}

.station-url {
  color: var(--color-text-secondary);
  font-size: var(--font-size-small);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.station-badge {
  color: var(--color-brand);
  font-size: var(--font-size-small);
  font-weight: 500;
}

.station-actions {
  display: flex;
  gap: var(--space-02);
}

.icon-btn {
  width: 40px;
  height: 40px;
  border-radius: var(--radius-full);
  border: 1px solid var(--color-border);
  background: var(--color-background-neutral);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  transition: all var(--transition-fast);
}

.icon-btn:hover {
  transform: scale(1.1);
}

.delete-btn:hover {
  background: rgba(255, 68, 68, 0.1);
  border-color: #ff4444;
}

.remove-image-btn:hover {
  background: rgba(255, 165, 0, 0.1);
  border-color: #ffa500;
}

/* Empty State */
.empty-state {
  padding: var(--space-06);
  text-align: center;
  color: var(--color-text-secondary);
  background: var(--color-background);
  border-radius: var(--radius-04);
  border: 2px dashed var(--color-border);
}

/* Delete Confirmation Modal */
.modal-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.7);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 5001;
}

.confirm-modal {
  background: var(--color-background-neutral-50);
  border-radius: var(--radius-07);
  padding: var(--space-06);
  max-width: 400px;
  width: 90%;
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

.confirm-modal h3 {
  color: var(--color-text);
  font-weight: 600;
}

.confirm-modal p {
  color: var(--color-text-secondary);
}

.confirm-actions {
  display: flex;
  gap: var(--space-03);
  justify-content: flex-end;
}

.btn-secondary,
.btn-danger {
  padding: var(--space-03) var(--space-05);
  border-radius: var(--radius-04);
  font-size: var(--font-size-body);
  cursor: pointer;
  transition: all var(--transition-fast);
  border: none;
}

.btn-secondary {
  background: var(--color-background-neutral);
  color: var(--color-text);
  border: 1px solid var(--color-border);
}

.btn-secondary:hover {
  background: var(--color-background);
}

.btn-danger {
  background: #ff4444;
  color: white;
}

.btn-danger:hover {
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(255, 68, 68, 0.3);
}

/* Responsive */
@media (max-width: 600px) {
  .station-item {
    flex-direction: column;
    align-items: flex-start;
  }

  .station-actions {
    align-self: flex-end;
  }
}
</style>
