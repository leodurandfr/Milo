<template>
  <div class="radio-settings-container">
    <section class="settings-section">
      <!-- Section 1: Unmodified Favorites -->
      <template v-if="unmodifiedFavorites.length > 0">
        <h2 class="heading-2 text-body">{{ $t('radioSettings.unmodifiedFavoritesTitle') }}</h2>

        <div class="stations-list">
          <StationCard v-for="station in unmodifiedFavorites" :key="station.id" :station="station" variant="card"
            :show-country="true" image-size="medium" @click="$emit('edit-station', station)" />
        </div>
      </template>

      <!-- Section 2: Modified Stations (from RadioBrowserAPI favorites) -->
      <h2 class="heading-2 text-body">{{ $t('radioSettings.modifiedStationsTitle') }}</h2>

      <div v-if="modifiedStations.length > 0" class="stations-list">
        <StationCard v-for="station in modifiedStations" :key="`${station.id}-${station.name}-${updateCounter}`" :station="station"
          variant="card" :show-country="true" image-size="medium">
          <template #actions>
            <CircularIcon icon="threeDots" variant="light" @click="$emit('edit-station', station)" />
            <CircularIcon icon="close" variant="light" @click="confirmRestoreStation(station)" />
          </template>
        </StationCard>
      </div>

      <div v-else class="empty-state text-mono">
        {{ $t('radioSettings.noModifiedStations') }}
      </div>


      <!-- Section 3: Added Stations (manually created) -->
      <h2 class="heading-2 text-body">{{ $t('radioSettings.addedStationsTitle') }}</h2>

      <div v-if="addedStations.length > 0" class="stations-list">
        <StationCard v-for="station in addedStations" :key="station.id" :station="station" variant="card"
          :show-country="true" image-size="medium">
          <template #actions>
            <CircularIcon icon="threeDots" variant="light" @click="$emit('edit-station', station)" />
            <CircularIcon icon="close" variant="light" @click="confirmDeleteStation(station)" />
          </template>
        </StationCard>
      </div>

      <div v-else class="empty-state text-mono">
        {{ $t('radioSettings.noAddedStations') }}
      </div>


      <Button variant="primary" @click="$emit('go-to-add-station')">
        {{ $t('radioSettings.addStation') }}
      </Button>
    </section>

    <!-- Delete Added Station Confirmation Modal -->
    <div v-if="stationToDelete" class="modal-overlay" @click.self="stationToDelete = null">
      <div class="confirm-modal">
        <h3 class="text-body">{{ $t('radioSettings.deleteStationConfirm') }}</h3>
        <p class="text-mono">{{ stationToDelete.name }}</p>
        <div class="confirm-actions">
          <Button variant="secondary" @click="stationToDelete = null">{{ $t('radioSettings.cancel') }}</Button>
          <button class="btn-danger" @click="deleteStation">{{ $t('radioSettings.delete') }}</button>
        </div>
      </div>
    </div>

    <!-- Restore Modified Station Confirmation Modal -->
    <div v-if="stationToRestore" class="modal-overlay" @click.self="stationToRestore = null">
      <div class="confirm-modal">
        <h3 class="text-body">{{ $t('radioSettings.restoreStationConfirm') }}</h3>
        <p class="text-mono">{{ stationToRestore.name }}</p>
        <p class="text-mono" style="color: var(--color-text-secondary); font-size: var(--font-size-small);">
          {{ $t('radioSettings.modificationsWillBeLost') }}
        </p>
        <div class="confirm-actions">
          <Button variant="secondary" @click="stationToRestore = null">{{ $t('radioSettings.cancel') }}</Button>
          <button class="btn-danger" @click="restoreStation">{{ $t('radioSettings.delete') }}</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue';
import axios from 'axios';
import { useRadioStore } from '@/stores/radioStore';
import Button from '@/components/ui/Button.vue';
import CircularIcon from '@/components/ui/CircularIcon.vue';
import StationCard from '@/components/audio/StationCard.vue';

defineEmits(['go-to-add-station', 'edit-station']);

const radioStore = useRadioStore();
const stationToDelete = ref(null);
const stationToRestore = ref(null);

// Local lists loaded from the API
const customStationsDict = ref({}); // Dict of station_id → custom metadata
const allFavorites = ref([]); // All favorites from API
const updateCounter = ref(0); // Force re-render counter

// Unmodified favorites: favorites that are NOT in customStationsDict
const unmodifiedFavorites = computed(() => {
  return allFavorites.value.filter(station => !customStationsDict.value[station.id]);
});

// Modified stations: RadioBrowser favorites that have been modified
// These are entries in customStationsDict with RadioBrowser UUID keys (not starting with "custom_")
const modifiedStations = computed(() => {
  const _ = updateCounter.value;
  return Object.entries(customStationsDict.value)
    .filter(([id, _]) => !id.startsWith('custom_'))
    .map(([id, metadata]) => ({ ...metadata, id }));
});

// Added stations: custom stations created manually
// These are entries in customStationsDict with keys starting with "custom_"
const addedStations = computed(() => {
  const _ = updateCounter.value;
  return Object.entries(customStationsDict.value)
    .filter(([id, _]) => id.startsWith('custom_'))
    .map(([id, metadata]) => ({ ...metadata, id }));
});

async function loadCustomStations() {
  // Load custom stations dict (contains both modified favorites and manually added stations)
  try {
    const customResponse = await axios.get('/api/radio/custom');
    customStationsDict.value = customResponse.data || {};
  } catch (error) {
    console.error('Erreur chargement stations personnalisées:', error);
    customStationsDict.value = {};
  }
}

async function loadAllFavorites() {
  // Load all favorites (including unmodified ones)
  try {
    const response = await axios.get('/api/radio/stations', {
      params: { favorites_only: true, limit: 10000 }
    });
    allFavorites.value = response.data.stations || [];
  } catch (error) {
    console.error('Erreur chargement favoris:', error);
    allFavorites.value = [];
  }
}

async function loadAllData() {
  await Promise.all([loadCustomStations(), loadAllFavorites()]);
}

// Expose loadCustomStations so SettingsModal can reload data
defineExpose({ loadCustomStations: loadAllData });

function confirmDeleteStation(station) {
  stationToDelete.value = station;
}

async function deleteStation() {
  if (!stationToDelete.value) return;

  const success = await radioStore.removeCustomStation(stationToDelete.value.id);

  if (success) {
    console.log('✅ Station supprimée');
    await loadAllData();
  } else {
    console.error('❌ Échec suppression station');
  }

  stationToDelete.value = null;
}

function confirmRestoreStation(station) {
  stationToRestore.value = station;
}

async function restoreStation() {
  if (!stationToRestore.value) return;

  // Call new endpoint to restore favorite metadata
  const formData = new FormData();
  formData.append('station_id', stationToRestore.value.id);

  try {
    const response = await axios.post('/api/radio/favorites/restore-metadata', formData);

    if (response.data.success) {
      console.log('✅ Station restaurée');
      // Wait a bit for backend to save, then reload
      await new Promise(resolve => setTimeout(resolve, 200));

      // Force reload all data
      await loadAllData();

      // Force re-render by incrementing counter
      updateCounter.value++;
    } else {
      console.error('❌ Échec restauration station');
    }
  } catch (error) {
    console.error('❌ Erreur restauration:', error);
  }

  stationToRestore.value = null;
}

onMounted(() => {
  loadAllData();
});
</script>

<style scoped>
.radio-settings-container {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.settings-section {
  background: var(--color-background-neutral);
  border-radius: var(--radius-06);
  padding: var(--space-05-fixed) var(--space-05);
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

/* Stations list */
.stations-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-01);
}

/* Empty state */
.empty-state {
  padding: var(--space-05);
  text-align: center;
  color: var(--color-text-secondary);
  background: var(--color-background);
  border-radius: var(--radius-04);
  border: 2px dashed var(--color-border);
}

/* Delete confirmation modal */
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
  .settings-section {
    border-radius: var(--radius-05);
  }

  .station-item {
    flex-direction: column;
    align-items: flex-start;
  }

  .station-actions {
    align-self: flex-end;
  }

  .stations-list {
    display: grid;
    grid-template-columns: repeat(1, minmax(0, 1fr));
    gap: var(--space-01);
  }

}
</style>
