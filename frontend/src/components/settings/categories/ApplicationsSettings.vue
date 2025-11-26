<!-- frontend/src/components/settings/categories/ApplicationsSettings.vue -->
<template>
  <div class="settings-container">

    <section class="settings-section">
      <!-- Audio sources -->
      <div class="setting-item-container">
        <p class="app-group-title text-mono">{{ t('audioSources.title') }}</p>

        <div class="app-list">
          <ListItemButton variant="outlined" :title="t('applications.spotify')" :class="{ 'active': config.librespot }">
            <template #icon>
              <AppIcon name="librespot" :size="40" />
            </template>
            <template #action>
              <Toggle v-model="config.librespot" size="compact"
                :disabled="!canDisableAudioSource('librespot')" @change="updateDockApps" />
            </template>
          </ListItemButton>

          <ListItemButton variant="outlined" :title="t('applications.bluetooth')" :class="{ 'active': config.bluetooth }">
            <template #icon>
              <AppIcon name="bluetooth" :size="40" />
            </template>
            <template #action>
              <Toggle v-model="config.bluetooth" size="compact"
                :disabled="!canDisableAudioSource('bluetooth')" @change="updateDockApps" />
            </template>
          </ListItemButton>

          <ListItemButton variant="outlined" :title="t('applications.macOS')" :class="{ 'active': config.roc }">
            <template #icon>
              <AppIcon name="roc" :size="40" />
            </template>
            <template #action>
              <Toggle v-model="config.roc" size="compact" :disabled="!canDisableAudioSource('roc')"
                @change="updateDockApps" />
            </template>
          </ListItemButton>

          <ListItemButton variant="outlined" :title="t('audioSources.radio')" :class="{ 'active': config.radio }">
            <template #icon>
              <AppIcon name="radio" :size="40" />
            </template>
            <template #action>
              <Toggle v-model="config.radio" size="compact"
                :disabled="!canDisableAudioSource('radio')" @change="updateDockApps" />
            </template>
          </ListItemButton>

          <ListItemButton variant="outlined" :title="t('podcasts.podcasts')" :class="{ 'active': config.podcast }">
            <template #icon>
              <AppIcon name="podcast" :size="40" />
            </template>
            <template #action>
              <Toggle v-model="config.podcast" size="compact"
                :disabled="!canDisableAudioSource('podcast')" @change="updateDockApps" />
            </template>
          </ListItemButton>
        </div>
      </div>
      <!-- Features -->
      <div class="setting-item-container">
        <p class="app-group-title text-mono">{{ t('applications.features') }}</p>

        <div class="app-list">
          <ListItemButton variant="outlined" :title="t('multiroom.title')" :class="{ 'active': config.multiroom }">
            <template #icon>
              <AppIcon name="multiroom" :size="40" />
            </template>
            <template #action>
              <Toggle v-model="config.multiroom" size="compact" @change="updateDockApps" />
            </template>
          </ListItemButton>

          <ListItemButton variant="outlined" :title="t('equalizer.title')" :class="{ 'active': config.equalizer }">
            <template #icon>
              <AppIcon name="equalizer" :size="40" />
            </template>
            <template #action>
              <Toggle v-model="config.equalizer" size="compact" @change="updateDockApps" />
            </template>
          </ListItemButton>

          <ListItemButton variant="outlined" :title="t('common.settings')" :class="{ 'active': config.settings }">
            <template #icon>
              <AppIcon name="settings" :size="40" />
            </template>
            <template #action>
              <Toggle v-model="config.settings" size="compact" @change="updateDockApps" />
            </template>
          </ListItemButton>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed, onMounted } from 'vue';
import { useI18n } from '@/services/i18n';
import useWebSocket from '@/services/websocket';
import { useSettingsAPI } from '@/composables/useSettingsAPI';
import { useSettingsStore } from '@/stores/settingsStore';
import ListItemButton from '@/components/ui/ListItemButton.vue';
import Toggle from '@/components/ui/Toggle.vue';
import AppIcon from '@/components/ui/AppIcon.vue';

const { t } = useI18n();
const { on } = useWebSocket();
const { debouncedUpdate } = useSettingsAPI();
const settingsStore = useSettingsStore();

// Using the store
const config = computed(() => settingsStore.dockApps);

function canDisableAudioSource(sourceId) {
  const audioSources = ['librespot', 'bluetooth', 'roc', 'radio', 'podcast'];
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
    // Update the store
    settingsStore.updateDockApps(enabledApps);
  }
};

onMounted(() => {
  // No need to load the config here; it's already in the store
  on('settings', 'dock_apps_changed', handleDockAppsChanged);
});
</script>

<style scoped>
.settings-container {
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
  gap: var(--space-05-fixed);
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
  gap: var(--space-01);
}

/* Override: buttons with a toggle keep the same background and border even when active */
.app-list :deep(.list-item-button.active) {
  background: var(--color-background);
  box-shadow: inset 0 0 0 1px var(--color-border);
}

/* Responsive */
@media (max-aspect-ratio: 4/3) {
  .settings-section {
    border-radius: var(--radius-05);
  }

  .app-list {
    display: flex;
    flex-direction: column;
  }
}
</style>