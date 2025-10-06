<!-- frontend/src/components/settings/categories/ScreenSettings.vue -->
<template>
  <div class="settings-container">
    <!-- Luminosité -->
    <section class="settings-section">
      <div class="screen-group">
        <h2 class="heading-2 text-body">{{ t('Luminosité') }}</h2>
        <div class="setting-item-container">
          <div class="screen-description text-mono">
            {{ t('Intensité de la luminosité') }}
          </div>
          <div class="brightness-control">
            <RangeSlider v-model="config.brightness_on" :min="1" :max="10" :step="1" value-unit=""
              @input="handleBrightnessChange" />
          </div>
        </div>
      </div>
    </section>

    <!-- Mise en veille automatique -->
    <section class="settings-section">
      <div class="screen-group">
        <h2 class="heading-2 text-body">{{ t('Mise en veille automatique') }}</h2>
        <div class="setting-item-container">
          <div class="screen-description text-mono">
            {{ t('Délai de la mise en veille après :') }}
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
import axios from 'axios';
import Button from '@/components/ui/Button.vue';
import RangeSlider from '@/components/ui/RangeSlider.vue';

const { t } = useI18n();
const { on } = useWebSocket();
const { updateSetting, loadConfig, clearAllTimers } = useSettingsAPI();

const config = ref({
  brightness_on: 5,
  timeout_enabled: true,
  timeout_seconds: 10
});

const timeoutPresets = computed(() => [
  { value: 10, label: t('10 secondes') },
  { value: 180, label: t('3 minutes') },
  { value: 900, label: t('15 minutes') },
  { value: 1800, label: t('30 minutes') },
  { value: 3600, label: t('1 heure') },
  { value: 0, label: t('Jamais') }
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
  }, 300);

  // Save to settings with debounce
  clearTimeout(brightnessDebounceTimeout);
  brightnessDebounceTimeout = setTimeout(() => {
    updateSetting('screen-brightness', { brightness_on: value });
  }, 1000);
}

function setScreenTimeout(value) {
  updateSetting('screen-timeout', {
    screen_timeout_enabled: value !== 0,
    screen_timeout_seconds: value
  });
}

// WebSocket listeners
const wsListeners = {
  screen_timeout_changed: (msg) => {
    if (msg.data?.config) {
      config.value.timeout_seconds = msg.data.config.screen_timeout_seconds;
      config.value.timeout_enabled = config.value.timeout_seconds !== 0;
    }
  },
  screen_brightness_changed: (msg) => {
    if (msg.data?.config?.brightness_on !== undefined) {
      config.value.brightness_on = msg.data.config.brightness_on;
    }
  }
};

onMounted(async () => {
  // Load configs
  const [screenTimeout, brightness] = await Promise.all([
    loadConfig('screen-timeout'),
    loadConfig('screen-brightness')
  ]);

  if (screenTimeout.status === 'success') {
    config.value.timeout_seconds = screenTimeout.config.screen_timeout_seconds ?? 10;
    config.value.timeout_enabled = config.value.timeout_seconds !== 0;
  }

  if (brightness.status === 'success') {
    config.value.brightness_on = brightness.config.brightness_on || 5;
  }

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
  padding: var(--space-06) var(--space-05);
  display: flex;
  flex-direction: column;
  gap: var(--space-05);
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
