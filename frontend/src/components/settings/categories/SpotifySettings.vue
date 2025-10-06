<!-- frontend/src/components/settings/categories/SpotifySettings.vue -->
<template>
  <section class="settings-section">
    <div class="spotify-group">
      <h2 class="heading-2 text-body">{{ t('Déconnexion automatique') }}</h2>
      <div class="setting-item-container">
        <div class="spotify-description text-mono">
          {{ t('Délai de déconnexion après que la musique soit en pause pendant :') }}
        </div>
        <div class="disconnect-buttons">
          <Button v-for="delay in disconnectPresets" :key="delay.value" variant="toggle"
            :active="isDisconnectActive(delay.value)" @click="setSpotifyDisconnect(delay.value)">
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

// Utilisation du store
const config = computed(() => ({
  auto_disconnect_delay: settingsStore.spotifyDisconnect.auto_disconnect_delay
}));

const disconnectPresets = computed(() => [
  { value: 10, label: t('10 secondes') },
  { value: 180, label: t('3 minutes') },
  { value: 900, label: t('15 minutes') },
  { value: 1800, label: t('30 minutes') },
  { value: 3600, label: t('1 heure') },
  { value: 0, label: t('Jamais') }
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

// WebSocket listener - met à jour le store
const handleSpotifyDisconnectChanged = (msg) => {
  if (msg.data?.config?.auto_disconnect_delay !== undefined) {
    settingsStore.updateSpotifyDisconnect({
      auto_disconnect_delay: msg.data.config.auto_disconnect_delay
    });
  }
};

onMounted(() => {
  // Plus besoin de charger la config, elle est déjà dans le store
  on('settings', 'spotify_disconnect_changed', handleSpotifyDisconnectChanged);
});
</script>

<style scoped>
.settings-section {
  background: var(--color-background-neutral);
  border-radius: var(--radius-04);
  padding: var(--space-06) var(--space-05);
  display: flex;
  flex-direction: column;
  gap: var(--space-05);
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
  .disconnect-buttons {
    display: flex;
    gap: var(--space-02);
    flex-wrap: wrap;
  }
}
</style>
