<template>
  <SettingsContainer>
    <!-- Track Recognition Toggle -->
    <ToggleSection
      :title="$t('radioSettings.trackRecognition')"
      :description="$t('radioSettings.trackRecognitionDescription')"
      :enabled="shazamEnabled"
      @change="handleShazamToggle"
    />

    <!-- Stations Management -->
    <SettingsSection>
    <!-- Section 1: Unmodified Favorites -->
    <template v-if="unmodifiedFavorites.length > 0">
      <h2 class="heading-2">{{ $t('radioSettings.unmodifiedFavoritesTitle') }}</h2>
      <div class="stations-list">
        <StationCard v-for="station in unmodifiedFavorites" :key="station.id" :station="station" variant="card"
          :show-country="true" @click="$emit('edit-station', station)" />
      </div>
    </template>

    <!-- Section 2: Modified Stations (from RadioBrowserAPI favorites) -->
    <h2 class="heading-2">{{ $t('radioSettings.modifiedStationsTitle') }}</h2>
    <div v-if="modifiedStations.length > 0" class="stations-list">
      <StationCard v-for="station in modifiedStations" :key="`${station.id}-${station.name}-${updateCounter}`" :station="station"
        variant="card" :show-country="true" @click="$emit('edit-station', { ...station, _canRestore: true })" />
    </div>
    <div v-else class="empty-state text-mono">
      {{ $t('radioSettings.noModifiedStations') }}
    </div>

    <!-- Section 3: Added Stations (manually created) -->
    <template v-if="addedStations.length > 0">
      <h2 class="heading-2">{{ $t('radioSettings.addedStationsTitle') }}</h2>
      <div class="stations-list">
        <StationCard v-for="station in addedStations" :key="station.id" :station="station" variant="card"
          :show-country="true" @click="$emit('edit-station', { ...station, _canDelete: true })" />
      </div>
    </template>

    <Button variant="brand" @click="$emit('go-to-add-station')">
      {{ $t('radioSettings.addStation') }}
    </Button>
    </SettingsSection>
  </SettingsContainer>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue';
import axios from 'axios';
import { useRadioStore } from '@/stores/radioStore';
import { useSettingsStore } from '@/stores/settingsStore';
import { useSettingsAPI } from '@/composables/useSettingsAPI';
import useWebSocket from '@/services/websocket';
import Button from '@/components/ui/Button.vue';
import ToggleSection from '@/components/settings/ToggleSection.vue';
import StationCard from '@/components/radio/StationCard.vue';
import SettingsContainer from '@/components/settings/SettingsContainer.vue';
import SettingsSection from '@/components/settings/SettingsSection.vue';

defineEmits(['go-to-add-station', 'edit-station']);

const radioStore = useRadioStore();
const settingsStore = useSettingsStore();
const { updateSetting } = useSettingsAPI();
const { on } = useWebSocket();

// Shazam toggle state (synced from settings store)
const shazamEnabled = computed(() => settingsStore.radioSettings.shazam_enabled);

async function handleShazamToggle(enabled) {
  settingsStore.updateRadioSettings({ shazam_enabled: enabled });
  await updateSetting('radio-settings', { shazam_enabled: enabled });
}

// Local lists loaded from the API
const customStationsDict = ref({}); // Dict of station_id → custom metadata
const allFavorites = ref([]); // All favorites from API
const updateCounter = ref(0); // Force re-render counter

// Unmodified favorites: favorites that are NOT in customStationsDict (sorted alphabetically)
const unmodifiedFavorites = computed(() => {
  return allFavorites.value
    .filter(station => !customStationsDict.value[station.id])
    .sort((a, b) => a.name.localeCompare(b.name));
});

// Modified stations: RadioBrowser favorites that have been modified (sorted alphabetically)
// These are entries in customStationsDict with RadioBrowser UUID keys (not starting with "custom_")
const modifiedStations = computed(() => {
  const _ = updateCounter.value;
  return Object.entries(customStationsDict.value)
    .filter(([id, _]) => !id.startsWith('custom_'))
    .map(([id, metadata]) => ({ ...metadata, id }))
    .sort((a, b) => a.name.localeCompare(b.name));
});

// Added stations: custom stations created manually (sorted alphabetically)
// These are entries in customStationsDict with keys starting with "custom_"
const addedStations = computed(() => {
  const _ = updateCounter.value;
  return Object.entries(customStationsDict.value)
    .filter(([id, _]) => id.startsWith('custom_'))
    .map(([id, metadata]) => ({ ...metadata, id }))
    .sort((a, b) => a.name.localeCompare(b.name));
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
      params: { favorites_only: true }
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

onMounted(() => {
  loadAllData();
});

// Listen for metadata modifications to auto-reload
on('radio', 'favorite_modified', () => {
  console.log('📻 Station modified, reloading RadioSettings data');
  loadAllData();
});

// Listen for radio settings changes (from other clients)
on('settings', 'radio_settings_changed', (msg) => {
  if (msg.data?.config) {
    settingsStore.updateRadioSettings(msg.data.config);
  }
});
</script>

<style scoped>
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

/* Responsive */
@media (max-aspect-ratio: 4/3) {
  .stations-list {
    grid-template-columns: repeat(1, minmax(0, 1fr));
  }
}
</style>
