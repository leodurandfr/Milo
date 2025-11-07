<template>
  <div class="radio-settings-container">
    <!-- Section 1: Stations with modified image -->
    <section class="settings-section">
      <h2 class="heading-2 text-body">{{ $t('radioSettings.modifiedImagesTitle') }}</h2>

      <div v-if="existingStationsWithModifiedImage.length > 0" class="stations-list">
        <StationCard v-for="station in existingStationsWithModifiedImage" :key="station.id" :station="station"
          variant="card" :show-country="true" image-size="medium">
          <template #actions>
            <CircularIcon icon="close" variant="neutral" @click="confirmRemoveImage(station)"
              :title="$t('radioSettings.restoreOriginalImage')" />
          </template>
        </StationCard>
      </div>

      <div v-else class="empty-state text-mono">
        {{ $t('radioSettings.noModifiedImages') }}
      </div>

      <Button variant="primary" @click="$emit('go-to-change-image')">
        {{ $t('radioSettings.changeStationImage') }}
      </Button>
    </section>

    <!-- Section 2: Added stations -->
    <section class="settings-section">
      <h2 class="heading-2 text-body">{{ $t('radioSettings.addedStationsTitle') }}</h2>

      <div v-if="customStations.length > 0" class="stations-list">
        <StationCard v-for="station in customStations" :key="station.id" :station="station" variant="card"
          :show-country="true" image-size="medium">
          <template #actions>
            <CircularIcon v-if="station.image_filename" icon="close" variant="neutral" @click="confirmRemoveImage(station)"
              :title="$t('radioSettings.removeImage')" />
            <CircularIcon icon="trash" variant="neutral" @click="confirmDelete(station)"
              :title="$t('radioSettings.deleteStation')" />
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

    <!-- Delete Confirmation Modal -->
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

    <!-- Remove Image Confirmation Modal -->
    <div v-if="stationToRemoveImage" class="modal-overlay" @click.self="stationToRemoveImage = null">
      <div class="confirm-modal">
        <h3 class="text-body">{{ $t('radioSettings.removeImageConfirm') }}</h3>
        <p class="text-mono">{{ stationToRemoveImage.name }}</p>
        <p class="text-mono" style="color: var(--color-text-secondary); font-size: var(--font-size-small);">
          {{ stationToRemoveImage.id.startsWith('custom_') ? $t('radioSettings.imageWillBeDeleted') : $t('radioSettings.imageWillBeDeletedAndRestored') }}
        </p>
        <div class="confirm-actions">
          <Button variant="secondary" @click="stationToRemoveImage = null">{{ $t('radioSettings.cancel') }}</Button>
          <button class="btn-danger" @click="removeImage">{{ $t('radioSettings.delete') }}</button>
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

defineEmits(['go-to-add-station', 'go-to-change-image']);

const radioStore = useRadioStore();
const stationToDelete = ref(null);
const stationToRemoveImage = ref(null);

// Local lists loaded from the API
const customStations = ref([]);
const favoriteStations = ref([]);

// Favorite stations with modified image (non-custom)
const existingStationsWithModifiedImage = computed(() => {
  return favoriteStations.value.filter(s =>
    s.image_filename && !s.id.startsWith('custom_')
  );
});

// Custom stations with image
const customStationsWithImage = computed(() => {
  return customStations.value.filter(s => s.image_filename);
});

// Custom stations without uploaded image
const customStationsWithoutImage = computed(() => {
  return customStations.value.filter(s => !s.image_filename);
});

async function loadCustomStations() {
  // Load custom stations
  try {
    const customResponse = await axios.get('/api/radio/custom');
    customStations.value = customResponse.data;
  } catch (error) {
    console.error('Erreur chargement stations personnalisées:', error);
    customStations.value = [];
  }

  // Load favorite stations to detect those with modified images
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

// Expose loadCustomStations so SettingsModal can reload data
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

onMounted(() => {
  loadCustomStations();
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
  border-radius: var(--radius-04);
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