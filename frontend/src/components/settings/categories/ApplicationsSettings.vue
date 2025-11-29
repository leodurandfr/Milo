<!-- frontend/src/components/settings/categories/ApplicationsSettings.vue -->
<template>
  <div class="settings-container">

    <section class="settings-section">
      <!-- Audio sources -->
      <div class="setting-item-container">
        <p class="app-group-title text-mono">{{ t('audioSources.title') }}</p>

        <div class="app-list">
          <ListItemButton
            :title="t('applications.spotify')"
            :model-value="config.librespot"
            :variant="config.librespot ? 'active' : 'inactive'"
            action="toggle"
            :disabled="!canDisableAudioSource('librespot')"
            @update:model-value="(val) => handleToggle('librespot', val)"
          >
            <template #icon>
              <AppIcon name="librespot" :size="40" />
            </template>
          </ListItemButton>

          <ListItemButton
            :title="t('applications.bluetooth')"
            :model-value="config.bluetooth"
            :variant="config.bluetooth ? 'active' : 'inactive'"
            action="toggle"
            :disabled="!canDisableAudioSource('bluetooth')"
            @update:model-value="(val) => handleToggle('bluetooth', val)"
          >
            <template #icon>
              <AppIcon name="bluetooth" :size="40" />
            </template>
          </ListItemButton>

          <ListItemButton
            :title="t('applications.macOS')"
            :model-value="config.roc"
            :variant="config.roc ? 'active' : 'inactive'"
            action="toggle"
            :disabled="!canDisableAudioSource('roc')"
            @update:model-value="(val) => handleToggle('roc', val)"
          >
            <template #icon>
              <AppIcon name="roc" :size="40" />
            </template>
          </ListItemButton>

          <ListItemButton
            :title="t('audioSources.radio')"
            :model-value="config.radio"
            :variant="config.radio ? 'active' : 'inactive'"
            action="toggle"
            :disabled="!canDisableAudioSource('radio')"
            @update:model-value="(val) => handleToggle('radio', val)"
          >
            <template #icon>
              <AppIcon name="radio" :size="40" />
            </template>
          </ListItemButton>

          <ListItemButton
            :title="t('podcasts.podcasts')"
            :model-value="config.podcast"
            :variant="config.podcast ? 'active' : 'inactive'"
            action="toggle"
            :disabled="!canDisableAudioSource('podcast')"
            @update:model-value="(val) => handleToggle('podcast', val)"
          >
            <template #icon>
              <AppIcon name="podcast" :size="40" />
            </template>
          </ListItemButton>
        </div>
      </div>
      <!-- Features -->
      <div class="setting-item-container">
        <p class="app-group-title text-mono">{{ t('applications.features') }}</p>

        <div class="app-list">
          <ListItemButton
            :title="t('multiroom.title')"
            :model-value="config.multiroom"
            :variant="config.multiroom ? 'active' : 'inactive'"
            action="toggle"
            @update:model-value="(val) => handleToggle('multiroom', val)"
          >
            <template #icon>
              <AppIcon name="multiroom" :size="40" />
            </template>
          </ListItemButton>

          <ListItemButton
            :title="t('equalizer.title')"
            :model-value="config.equalizer"
            :variant="config.equalizer ? 'active' : 'inactive'"
            action="toggle"
            @update:model-value="(val) => handleToggle('equalizer', val)"
          >
            <template #icon>
              <AppIcon name="equalizer" :size="40" />
            </template>
          </ListItemButton>

          <ListItemButton
            :title="t('common.settings')"
            :model-value="config.settings"
            :variant="config.settings ? 'active' : 'inactive'"
            action="toggle"
            @update:model-value="(val) => handleToggle('settings', val)"
          >
            <template #icon>
              <AppIcon name="settings" :size="40" />
            </template>
          </ListItemButton>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup>
import { onMounted } from 'vue';
import { storeToRefs } from 'pinia';
import { useI18n } from '@/services/i18n';
import useWebSocket from '@/services/websocket';
import { useSettingsAPI } from '@/composables/useSettingsAPI';
import { useSettingsStore } from '@/stores/settingsStore';
import ListItemButton from '@/components/ui/ListItemButton.vue';
import AppIcon from '@/components/ui/AppIcon.vue';

const { t } = useI18n();
const { on } = useWebSocket();
const { debouncedUpdate } = useSettingsAPI();
const settingsStore = useSettingsStore();

// Using storeToRefs for proper reactivity
const { dockApps: config } = storeToRefs(settingsStore);

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

function handleToggle(appName, value) {
  config.value[appName] = value;
  updateDockApps();
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