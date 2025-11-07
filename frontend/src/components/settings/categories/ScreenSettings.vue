<!-- frontend/src/components/settings/categories/ScreenSettings.vue -->
<template>
  <div class="settings-container">
    <!-- Brightness -->
    <section class="settings-section">
      <div class="screen-group">
        <h2 class="heading-2 text-body">{{ t('screenSettings.brightness') }}</h2>
        <div class="setting-item-container">
          <div class="screen-description text-mono">
            {{ t('screenSettings.brightnessIntensity') }}
          </div>
          <div class="brightness-control">
            <RangeSlider v-model="config.brightness_on" :min="1" :max="10" :step="1" value-unit=""
              @input="handleBrightnessChange" />
          </div>
        </div>
      </div>
    </section>

    <!-- Auto sleep -->
    <section class="settings-section">
      <div class="screen-group">
        <h2 class="heading-2 text-body">{{ t('screenSettings.autoSleep') }}</h2>
        <div class="setting-item-container">
          <div class="screen-description text-mono">
            {{ t('screenSettings.sleepDelay') }}
          </div>
          <div class="timeout-buttons">
            <Button v-for="timeout in timeoutPresets" :key="timeout.value" variant="toggle"
              :active="isTimeoutActive(timeout.value)" @click="setScreenTimeout(timeout.value)">
              {{ timeout.label }}
            </Button>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue';
import { useI18n } from '@/services/i18n';
import useWebSocket from '@/services/websocket';
import { useSettingsAPI } from '@/composables/useSettingsAPI';
import { useSettingsStore } from '@/stores/settingsStore';
import axios from 'axios';
import Button from '@/components/ui/Button.vue';
import RangeSlider from '@/components/ui/RangeSlider.vue';

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
  { value: 10, label: t('time.10seconds') },
  { value: 180, label: t('time.3minutes') },
  { value: 900, label: t('time.15minutes') },
  { value: 1800, label: t('time.30minutes') },
  { value: 3600, label: t('time.1hour') },
  { value: 0, label: t('time.never') }
]);

function isTimeoutActive(value) {
  if (value === 0) {
    return config.value.timeout_seconds === 0;
  }
  return config.value.timeout_seconds === value;
}

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

<style scoped>
.settings-container {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.settings-section {
  background: var(--color-background-neutral);
  border-radius: var(--radius-04);
  padding: var(--space-05-fixed) var(--space-05);
  display: flex;
  flex-direction: column;
  gap: var(--space-05-fixed);
}

.screen-group {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

.setting-item-container {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

.screen-description {
  color: var(--color-text-secondary);
}

.brightness-control {
  display: flex;
  align-items: center;
}

.timeout-buttons {
  display: flex;
  gap: var(--space-02);
  flex-wrap: wrap;
}

.timeout-buttons .btn {
  flex: 1;
  min-width: 150px;
}

/* Responsive */
@media (max-aspect-ratio: 4/3) {
  .timeout-buttons {
    display: flex;
    gap: var(--space-02);
    flex-wrap: wrap;
  }

  .brightness-control {
    gap: var(--space-05);
  }
}
</style>