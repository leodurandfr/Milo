<template>
  <SettingsContainer>
    <!-- Track Recognition Toggle -->
    <ToggleSection
      :title="t('radioSettings.trackRecognition')"
      :description="t('radioSettings.trackRecognitionDescription')"
      :enabled="shazamEnabled"
      @change="handleShazamToggle"
    />

    <!-- Stations Management -->
    <SettingsSection>
    <!-- Section 1: Unmodified Favorites -->
    <template v-if="unmodifiedFavorites.length > 0">
      <h2 class="heading-2">{{ t('radioSettings.unmodifiedFavoritesTitle') }}</h2>
      <div class="stations-list">
        <StationCard v-for="station in unmodifiedFavorites" :key="station.id" :station="station" variant="card"
          :show-country="true" @click="$emit('edit-station', station)" />
      </div>
    </template>

    <!-- Section 2: Modified Stations (from RadioBrowserAPI favorites) -->
    <h2 class="heading-2">{{ t('radioSettings.modifiedStationsTitle') }}</h2>
    <div v-if="modifiedStations.length > 0" class="stations-list">
      <StationCard v-for="station in modifiedStations" :key="station.id" :station="station"
        variant="card" :show-country="true" @click="$emit('edit-station', { ...station, _canRestore: true })" />
    </div>
    <div v-else class="empty-state text-mono">
      {{ t('radioSettings.noModifiedStations') }}
    </div>

    <!-- Section 3: Added Stations (manually created) -->
    <template v-if="addedStations.length > 0">
      <h2 class="heading-2">{{ t('radioSettings.addedStationsTitle') }}</h2>
      <div class="stations-list">
        <StationCard v-for="station in addedStations" :key="station.id" :station="station" variant="card"
          :show-country="true" @click="$emit('edit-station', { ...station, _canDelete: true })" />
      </div>
    </template>

    <Button variant="brand" @click="$emit('go-to-add-station')">
      {{ t('radioSettings.addStation') }}
    </Button>
    </SettingsSection>
  </SettingsContainer>
</template>

<script setup>
import { computed, onMounted } from 'vue';
import { useI18n } from '@/services/i18n';
import { useRadioStore } from '@/stores/radioStore';
import { useSettingsStore } from '@/stores/settingsStore';
import { useSettingsAPI } from '@/composables/useSettingsAPI';
import useWebSocket from '@/services/websocket';
import { logger } from '@/services/logger';
import Button from '@/components/ui/Button.vue';
import ToggleSection from '@/components/ui/ToggleSection.vue';
import StationCard from '@/components/radio/StationCard.vue';
import SettingsContainer from '@/components/settings/SettingsContainer.vue';
import SettingsSection from '@/components/settings/SettingsSection.vue';

defineEmits(['go-to-add-station', 'edit-station']);

const { t } = useI18n();
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

// Unmodified favorites: favorites that are NOT in customStations (already sorted by store)
const unmodifiedFavorites = computed(() => {
  return radioStore.favoriteStations
    .filter(station => !radioStore.customStations[station.id]);
});

// Modified stations: RadioBrowser favorites that have been modified (sorted alphabetically)
const modifiedStations = computed(() => {
  return Object.entries(radioStore.customStations)
    .filter(([id]) => !id.startsWith('custom_'))
    .map(([id, metadata]) => ({ ...metadata, id }))
    .sort((a, b) => a.name.localeCompare(b.name));
});

// Added stations: custom stations created manually (sorted alphabetically)
const addedStations = computed(() => {
  return Object.entries(radioStore.customStations)
    .filter(([id]) => id.startsWith('custom_'))
    .map(([id, metadata]) => ({ ...metadata, id }))
    .sort((a, b) => a.name.localeCompare(b.name));
});

onMounted(() => {
  // Refresh data on mount (preloaded data prevents layout shift, this ensures freshness)
  radioStore.loadRadioSettingsData();
});

// Listen for metadata modifications to refresh custom stations
// (favorites are already updated in real-time via RadioSource's WebSocket handlers)
on('plugin', 'favorite_modified', (event) => {
  if (event.data?.source === 'radio') {
    logger.debug('radio', 'Station modified, reloading custom stations for settings');
    radioStore.loadRadioSettingsData();
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
