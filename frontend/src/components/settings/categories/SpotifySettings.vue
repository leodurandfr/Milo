<!-- frontend/src/components/settings/categories/SpotifySettings.vue -->
<template>
  <section class="settings-section">
    <div class="spotify-group">
      <h2 class="heading-2">{{ t('spotifySettings.autoDisconnect') }}</h2>
      <div class="setting-item-container">
        <div class="spotify-description text-mono">
          {{ t('spotifySettings.disconnectDelay') }}
        </div>
        <ButtonGroup
          :model-value="config.auto_disconnect_delay"
          :options="disconnectPresets"
          mobile-layout="grid-3"
          :last-full-width="true"
          @change="setSpotifyDisconnect"
        />
      </div>
    </div>
  </section>
</template>

<script setup>
import { computed, onMounted } from 'vue';
import { useI18n } from '@/services/i18n';
import useWebSocket from '@/services/websocket';
import { useSettingsAPI } from '@/composables/useSettingsAPI';
import { useSettingsStore } from '@/stores/settingsStore';
import ButtonGroup from '@/components/ui/ButtonGroup.vue';

const { t } = useI18n();
const { on } = useWebSocket();
const { updateSetting } = useSettingsAPI();
const settingsStore = useSettingsStore();

// Using the store
const config = computed(() => ({
  auto_disconnect_delay: settingsStore.spotifyDisconnect.auto_disconnect_delay
}));

const disconnectPresets = computed(() => [
  { value: 15, label: t('time.15sec') },
  { value: 120, label: t('time.2min') },
  { value: 300, label: t('time.5min') },
  { value: 900, label: t('time.15min') },
  { value: 1800, label: t('time.30min') },
  { value: 3600, label: t('time.1h') },
  { value: 0, label: t('time.never') }
]);

function setSpotifyDisconnect(value) {
  updateSetting('spotify-disconnect', { auto_disconnect_delay: value });
}

// WebSocket listener - updates the store
const handleSpotifyDisconnectChanged = (msg) => {
  if (msg.data?.config?.auto_disconnect_delay !== undefined) {
    settingsStore.updateSpotifyDisconnect({
      auto_disconnect_delay: msg.data.config.auto_disconnect_delay
    });
  }
};

onMounted(() => {
  // No need to load the config, it's already in the store
  on('settings', 'spotify_disconnect_changed', handleSpotifyDisconnectChanged);
});
</script>

<style scoped>
.settings-section {
  background: var(--color-background-neutral);
  border-radius: var(--radius-06);
  padding: var(--space-05-fixed) var(--space-05);
  display: flex;
  flex-direction: column;
  gap: var(--space-05-fixed);
}

.spotify-group {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

.setting-item-container {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

.spotify-description {
  color: var(--color-text-secondary);
}

/* Responsive */
@media (max-aspect-ratio: 4/3) {
  .settings-section {
    border-radius: var(--radius-05);
  }
}
</style>