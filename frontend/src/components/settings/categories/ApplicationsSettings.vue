<!-- frontend/src/components/settings/categories/ApplicationsSettings.vue -->
<template>
  <section class="settings-section">
    <!-- Sources audio -->
    <div class="setting-item-container">
      <p class="app-group-title text-mono">{{ t('Sources audio') }}</p>

      <div class="app-list">
        <div class="app-item">
          <div class="app-info">
            <AppIcon name="librespot" :size="32" />
            <span class="app-name text-body">Spotify</span>
          </div>
          <Toggle v-model="config.librespot" variant="primary" size="compact"
            :disabled="!canDisableAudioSource('librespot')" @change="updateDockApps" />
        </div>

        <div class="app-item">
          <div class="app-info">
            <AppIcon name="bluetooth" :size="32" />
            <span class="app-name text-body">Bluetooth</span>
          </div>
          <Toggle v-model="config.bluetooth" variant="primary" size="compact"
            :disabled="!canDisableAudioSource('bluetooth')" @change="updateDockApps" />
        </div>

        <div class="app-item">
          <div class="app-info">
            <AppIcon name="roc" :size="32" />
            <span class="app-name text-body">{{ t('macOS') }}</span>
          </div>
          <Toggle v-model="config.roc" variant="primary" size="compact"
            :disabled="!canDisableAudioSource('roc')" @change="updateDockApps" />
        </div>
      </div>
    </div>

    <!-- Fonctionnalités -->
    <div class="setting-item-container">
      <p class="app-group-title text-mono">{{ t('Fonctionnalités') }}</p>

      <div class="app-list">
        <div class="app-item">
          <div class="app-info">
            <AppIcon name="multiroom" :size="32" />
            <span class="app-name text-body">Multiroom</span>
          </div>
          <Toggle v-model="config.multiroom" variant="primary" size="compact"
            @change="updateDockApps" />
        </div>

        <div class="app-item">
          <div class="app-info">
            <AppIcon name="equalizer" :size="32" />
            <span class="app-name text-body">{{ t('Égaliseur') }}</span>
          </div>
          <Toggle v-model="config.equalizer" variant="primary" size="compact"
            @change="updateDockApps" />
        </div>

        <div class="app-item">
          <div class="app-info">
            <AppIcon name="settings" :size="32" />
            <span class="app-name text-body">{{ t('Paramètres') }}</span>
          </div>
          <Toggle v-model="config.settings" variant="primary" size="compact" @change="updateDockApps" />
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
import Toggle from '@/components/ui/Toggle.vue';
import AppIcon from '@/components/ui/AppIcon.vue';

const { t } = useI18n();
const { on } = useWebSocket();
const { debouncedUpdate } = useSettingsAPI();
const settingsStore = useSettingsStore();

// Utilisation du store
const config = computed(() => settingsStore.dockApps);

function canDisableAudioSource(sourceId) {
  const audioSources = ['librespot', 'bluetooth', 'roc'];
  const enabledAudioSources = audioSources.filter(source =>
    config.value[source] && source !== sourceId
  );
  return enabledAudioSources.length > 0;
}

function getEnabledAppsArray() {
  return Object.keys(config.value).filter(app => config.value[app]);
}

function updateDockApps() {
  const enabledApps = getEnabledAppsArray();
  debouncedUpdate('dock-apps', 'dock-apps', { enabled_apps: enabledApps }, 500);
}

// WebSocket listener
const handleDockAppsChanged = (msg) => {
  if (msg.data?.config?.enabled_apps) {
    const enabledApps = msg.data.config.enabled_apps;
    // Mettre à jour le store
    settingsStore.updateDockApps(enabledApps);
  }
};

onMounted(() => {
  // Plus besoin de charger la config, elle est déjà dans le store
  on('settings', 'dock_apps_changed', handleDockAppsChanged);
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

.setting-item-container {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

.app-group-title {
  color: var(--color-text-secondary);
  font-weight: 500;
}

.app-list {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-02);
}

.app-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: var(--space-02);
  background: var(--color-background-strong);
  border-radius: var(--radius-04);
}

.app-info {
  display: flex;
  align-items: center;
  gap: var(--space-02);
}

.app-name {
  color: var(--color-text);
}

/* Responsive */
@media (max-aspect-ratio: 4/3) {
  .app-list {
    display: flex;
    flex-direction: column;
    gap: var(--space-02);
  }

  .app-item {
    padding: var(--space-02);
  }
}
</style>
