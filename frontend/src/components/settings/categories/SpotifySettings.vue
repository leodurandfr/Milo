<!-- frontend/src/components/settings/categories/SpotifySettings.vue -->
<template>
  <section class="settings-section">
    <div class="spotify-group">
      <h2 class="heading-2 heading-3">{{ t('spotifySettings.autoDisconnect') }}</h2>
      <div class="setting-item-container">
        <div class="spotify-description text-mono">
          {{ t('spotifySettings.disconnectDelay') }}
        </div>
        <div class="disconnect-buttons">
          <Button v-for="delay in disconnectPresets" :key="delay.value"
            :variant="isDisconnectActive(delay.value) ? 'outline' : 'background-strong'" size="medium"
            @click="setSpotifyDisconnect(delay.value)">
            {{ delay.label }}
          </Button>
        </div>
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
import Button from '@/components/ui/Button.vue';

const { t } = useI18n();
const { on } = useWebSocket();
const { updateSetting } = useSettingsAPI();
const settingsStore = useSettingsStore();

// Using the store
const config = computed(() => ({
  auto_disconnect_delay: settingsStore.spotifyDisconnect.auto_disconnect_delay
}));

const disconnectPresets = computed(() => [
  { value: 10, label: t('time.10seconds') },
  { value: 180, label: t('time.3minutes') },
  { value: 900, label: t('time.15minutes') },
  { value: 1800, label: t('time.30minutes') },
  { value: 3600, label: t('time.1hour') },
  { value: 0, label: t('time.never') }
]);

function isDisconnectActive(value) {
  if (value === 0) {
    return config.value.auto_disconnect_delay === 0;
  }
  return config.value.auto_disconnect_delay === value;
}

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

.disconnect-buttons {
  display: flex;
  gap: var(--space-02);
  flex-wrap: wrap;
}

.disconnect-buttons .btn {
  flex: 1;
  min-width: 150px;
}

/* Responsive */
@media (max-aspect-ratio: 4/3) {
  .settings-section {
    border-radius: var(--radius-05);
  }

  .disconnect-buttons {
    display: flex;
    gap: var(--space-02);
    flex-wrap: wrap;
  }
}
</style>