<!-- frontend/src/components/settings/categories/ApplicationsSettings.vue -->
<template>
  <SettingsContainer>
    <SettingsSection>
      <!-- Audio sources -->
      <SettingItem :label="t('audioSources.title')">
        <div class="app-list">
          <ListItemButton
            :title="t('applications.spotify')"
            :model-value="config.spotify"
            variant="background"
            action="toggle"
            :disabled="!canDisableAudioSource('spotify')"
            @update:model-value="(val) => handleToggle('spotify', val)"
          >
            <template #icon>
              <AppIcon name="spotify" :size="40" />
            </template>
          </ListItemButton>

          <ListItemButton
            :title="t('applications.bluetooth')"
            :model-value="config.bluetooth"
            variant="background"
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
            :model-value="config.mac"
            variant="background"
            action="toggle"
            :disabled="!canDisableAudioSource('mac')"
            @update:model-value="(val) => handleToggle('mac', val)"
          >
            <template #icon>
              <AppIcon name="mac" :size="40" />
            </template>
          </ListItemButton>

          <ListItemButton
            :title="t('audioSources.radio')"
            :model-value="config.radio"
            variant="background"
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
            variant="background"
            action="toggle"
            :disabled="!canDisableAudioSource('podcast')"
            @update:model-value="(val) => handleToggle('podcast', val)"
          >
            <template #icon>
              <AppIcon name="podcast" :size="40" />
            </template>
          </ListItemButton>
        </div>
      </SettingItem>

      <!-- Features -->
      <SettingItem :label="t('applications.features')">
        <div class="app-list">
          <ListItemButton
            :title="t('multiroom.title')"
            :model-value="config.multiroom"
            variant="background"
            action="toggle"
            @update:model-value="(val) => handleToggle('multiroom', val)"
          >
            <template #icon>
              <AppIcon name="multiroom" :size="40" />
            </template>
          </ListItemButton>

          <ListItemButton
            :title="t('common.settings')"
            :model-value="config.settings"
            variant="background"
            action="toggle"
            @update:model-value="(val) => handleToggle('settings', val)"
          >
            <template #icon>
              <AppIcon name="settings" :size="40" />
            </template>
          </ListItemButton>
        </div>
      </SettingItem>
    </SettingsSection>
  </SettingsContainer>
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
import SettingsContainer from '@/components/settings/SettingsContainer.vue';
import SettingsSection from '@/components/settings/SettingsSection.vue';
import SettingItem from '@/components/settings/SettingItem.vue';

const { t } = useI18n();
const { on } = useWebSocket();
const { debouncedUpdate } = useSettingsAPI();
const settingsStore = useSettingsStore();

// Using storeToRefs for proper reactivity
const { dockApps: config } = storeToRefs(settingsStore);

function canDisableAudioSource(sourceId) {
  const audioSources = ['spotify', 'bluetooth', 'mac', 'radio', 'podcast'];
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
.app-list {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-01);
}

@media (max-aspect-ratio: 4/3) {
  .app-list {
    display: flex;
    flex-direction: column;
  }
}
</style>
