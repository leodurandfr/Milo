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

          <ListItemButton
            :title="t('applications.airplay')"
            :model-value="config.airplay"
            variant="background"
            action="toggle"
            :disabled="!canDisableAudioSource('airplay')"
            @update:model-value="(val) => handleToggle('airplay', val)"
          >
            <template #icon>
              <AppIcon name="airplay" :size="40" />
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

    <!-- Inactivity timeout -->
    <ToggleSection
      :title="t('applicationsSettings.inactivityTimeout')"
      :enabled="inactivityEnabled"
      @change="handleInactivityToggle"
    >
      <SettingItem :label="t('applicationsSettings.inactivityDelay')">
        <ButtonGroup
          :model-value="inactivityConfig.inactivity_timeout"
          :options="inactivityPresets"
          mobile-layout="grid-3"
          @change="setInactivityTimeout"
        />
      </SettingItem>
    </ToggleSection>
  </SettingsContainer>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue';
import { storeToRefs } from 'pinia';
import { useI18n } from '@/services/i18n';
import useWebSocket from '@/services/websocket';
import { useSettingsAPI } from '@/composables/useSettingsAPI';
import { useSettingsStore } from '@/stores/settingsStore';
import ListItemButton from '@/components/ui/ListItemButton.vue';
import AppIcon from '@/components/ui/AppIcon.vue';
import ButtonGroup from '@/components/ui/ButtonGroup.vue';
import SettingsContainer from '@/components/settings/SettingsContainer.vue';
import SettingsSection from '@/components/settings/SettingsSection.vue';
import SettingItem from '@/components/settings/SettingItem.vue';
import ToggleSection from '@/components/settings/ToggleSection.vue';

const { t } = useI18n();
const { on } = useWebSocket();
const { debouncedUpdate, updateSetting } = useSettingsAPI();
const settingsStore = useSettingsStore();

// Using storeToRefs for proper reactivity
const { dockApps: config } = storeToRefs(settingsStore);

// === Dock Apps ===

function canDisableAudioSource(sourceId) {
  const audioSources = ['spotify', 'bluetooth', 'radio', 'podcast', 'airplay', 'mac'];
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

// === Inactivity Timeout ===

const inactivityConfig = ref({
  inactivity_timeout: 7200
});

const inactivityEnabled = computed(() => inactivityConfig.value.inactivity_timeout !== 0);

const lastNonZeroTimeout = ref(7200);

function syncInactivityFromStore() {
  const timeout = settingsStore.inactivityTimeout.inactivity_timeout;
  inactivityConfig.value.inactivity_timeout = timeout;
  if (timeout > 0) {
    lastNonZeroTimeout.value = timeout;
  }
}

const inactivityPresets = computed(() => [
  { value: 300, label: t('time.5min') },
  { value: 900, label: t('time.15min') },
  { value: 3600, label: t('time.1h') },
  { value: 7200, label: t('time.2h') },
  { value: 14400, label: t('time.4h') }
]);

function handleInactivityToggle(enabled) {
  if (enabled) {
    inactivityConfig.value.inactivity_timeout = lastNonZeroTimeout.value;
    settingsStore.updateInactivityTimeout({ inactivity_timeout: lastNonZeroTimeout.value });
    updateSetting('inactivity-timeout', { inactivity_timeout: lastNonZeroTimeout.value });
  } else {
    if (inactivityConfig.value.inactivity_timeout > 0) {
      lastNonZeroTimeout.value = inactivityConfig.value.inactivity_timeout;
    }
    inactivityConfig.value.inactivity_timeout = 0;
    settingsStore.updateInactivityTimeout({ inactivity_timeout: 0 });
    updateSetting('inactivity-timeout', { inactivity_timeout: 0 });
  }
}

function setInactivityTimeout(value) {
  if (value > 0) {
    lastNonZeroTimeout.value = value;
  }
  inactivityConfig.value.inactivity_timeout = value;
  settingsStore.updateInactivityTimeout({ inactivity_timeout: value });
  updateSetting('inactivity-timeout', { inactivity_timeout: value });
}

// === WebSocket listeners ===

const handleDockAppsChanged = (msg) => {
  if (msg.data?.config?.enabled_apps) {
    settingsStore.updateDockApps(msg.data.config.enabled_apps);
  }
};

const handleInactivityTimeoutChanged = (msg) => {
  if (msg.data?.config?.inactivity_timeout !== undefined) {
    const timeout = msg.data.config.inactivity_timeout;
    settingsStore.updateInactivityTimeout({ inactivity_timeout: timeout });
    inactivityConfig.value.inactivity_timeout = timeout;
    if (timeout > 0) {
      lastNonZeroTimeout.value = timeout;
    }
  }
};

onMounted(() => {
  syncInactivityFromStore();
  on('settings', 'dock_apps_changed', handleDockAppsChanged);
  on('settings', 'inactivity_timeout_changed', handleInactivityTimeoutChanged);
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
