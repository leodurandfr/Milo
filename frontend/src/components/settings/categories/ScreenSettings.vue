<!-- frontend/src/components/settings/categories/ScreenSettings.vue -->
<template>
  <SettingsContainer>
    <!-- Brightness -->
    <SettingsSection :title="t('screenSettings.brightness')">
      <SettingItem :label="t('screenSettings.brightnessIntensity')">
        <RangeSlider v-model="config.brightness_on" :min="1" :max="10" :step="1" value-unit=""
          @input="handleBrightnessChange" />
      </SettingItem>
    </SettingsSection>

    <!-- Auto sleep -->
    <SettingsSection :title="t('screenSettings.autoSleep')">
      <SettingItem :label="t('screenSettings.sleepDelay')">
        <ButtonGroup
          :model-value="config.timeout_seconds"
          :options="timeoutPresets"
          mobile-layout="grid-3"
          :last-full-width="true"
          @change="setScreenTimeout"
        />
      </SettingItem>
    </SettingsSection>
  </SettingsContainer>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue';
import { useI18n } from '@/services/i18n';
import useWebSocket from '@/services/websocket';
import { useSettingsAPI } from '@/composables/useSettingsAPI';
import { useSettingsStore } from '@/stores/settingsStore';
import axios from 'axios';
import ButtonGroup from '@/components/ui/ButtonGroup.vue';
import RangeSlider from '@/components/ui/RangeSlider.vue';
import SettingsContainer from '@/components/settings/SettingsContainer.vue';
import SettingsSection from '@/components/settings/SettingsSection.vue';
import SettingItem from '@/components/settings/SettingItem.vue';

const { t } = useI18n();
const { on } = useWebSocket();
const { updateSetting, clearAllTimers } = useSettingsAPI();
const settingsStore = useSettingsStore();

// Local refs for instant responsiveness
const config = ref({
  brightness_on: 5,
  timeout_enabled: true,
  timeout_seconds: 900
});

// Sync local refs with the store on mount
function syncFromStore() {
  config.value.brightness_on = settingsStore.screenBrightness.brightness_on;
  config.value.timeout_enabled = settingsStore.screenTimeout.screen_timeout_enabled;
  config.value.timeout_seconds = settingsStore.screenTimeout.screen_timeout_seconds;
}

const timeoutPresets = computed(() => [
  { value: 15, label: t('time.15sec') },
  { value: 120, label: t('time.2min') },
  { value: 300, label: t('time.5min') },
  { value: 900, label: t('time.15min') },
  { value: 1800, label: t('time.30min') },
  { value: 3600, label: t('time.1h') },
  { value: 0, label: t('time.never') }
]);

let brightnessInstantTimeout = null;
let brightnessDebounceTimeout = null;

function handleBrightnessChange(value) {
  // Apply immediately for instant feedback
  clearTimeout(brightnessInstantTimeout);
  brightnessInstantTimeout = setTimeout(() => {
    axios.post('/api/settings/screen-brightness/apply', { brightness_on: value }).catch(console.error);
  }, 50);

  // Save to settings with debounce
  clearTimeout(brightnessDebounceTimeout);
  brightnessDebounceTimeout = setTimeout(() => {
    updateSetting('screen-brightness', { brightness_on: value });
  }, 50);
}

function setScreenTimeout(value) {
  updateSetting('screen-timeout', {
    screen_timeout_enabled: value !== 0,
    screen_timeout_seconds: value
  });
}

// WebSocket listeners - update both the store AND local refs
const wsListeners = {
  screen_timeout_changed: (msg) => {
    if (msg.data?.config) {
      settingsStore.updateScreenTimeout({
        screen_timeout_seconds: msg.data.config.screen_timeout_seconds,
        screen_timeout_enabled: msg.data.config.screen_timeout_seconds !== 0
      });
      config.value.timeout_seconds = msg.data.config.screen_timeout_seconds;
      config.value.timeout_enabled = msg.data.config.screen_timeout_seconds !== 0;
    }
  },
  screen_brightness_changed: (msg) => {
    if (msg.data?.config?.brightness_on !== undefined) {
      settingsStore.updateScreenBrightness({
        brightness_on: msg.data.config.brightness_on
      });
      config.value.brightness_on = msg.data.config.brightness_on;
    }
  }
};

onMounted(() => {
  // Sync with the store on mount
  syncFromStore();

  // Register WebSocket listeners
  Object.entries(wsListeners).forEach(([eventType, handler]) => {
    on('settings', eventType, handler);
  });
});

onUnmounted(() => {
  clearTimeout(brightnessInstantTimeout);
  clearTimeout(brightnessDebounceTimeout);
  clearAllTimers();
});
</script>
