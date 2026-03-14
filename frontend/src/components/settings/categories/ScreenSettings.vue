<!-- frontend/src/components/settings/categories/ScreenSettings.vue -->
<template>
  <SettingsContainer>
    <!-- Brightness -->
    <SettingsSection :title="t('screenSettings.brightness')">
      <SettingItem :label="t('screenSettings.brightnessIntensity')">
        <RangeSlider v-model="config.brightness_on" :min="1" :max="10" :step="1" value-unit=""
          @input="handleBrightnessChange" @change="saveBrightness" />
      </SettingItem>
    </SettingsSection>

    <!-- UI Scale (kiosk only) -->
    <SettingsSection :title="t('screenSettings.uiScale')">
      <SettingItem :label="t('screenSettings.uiScaleLevel')">
        <ButtonGroup
          :model-value="config.ui_scale"
          :options="uiScalePresets"
          @change="setUiScale"
        />
      </SettingItem>
    </SettingsSection>

    <!-- Screensaver -->
    <ToggleSection
      :title="t('screenSettings.screensaver')"
      :enabled="config.screensaver_enabled"
      @change="handleScreensaverToggle"
    >
      <div class="screensaver-content">
        <p class="screensaver-source-note text-mono">{{ t('screenSettings.screensaverSourceNote') }}</p>
        <SettingItem :label="t('screenSettings.screensaverDelay')">
          <ButtonGroup
            :model-value="config.screensaver_delay_seconds"
            :options="sharedDelayPresets"
            mobile-layout="grid-3"
            @change="setScreensaverDelay"
          />
        </SettingItem>
      </div>
    </ToggleSection>

    <!-- Auto sleep -->
    <ToggleSection
      :title="t('screenSettings.autoSleep')"
      :enabled="config.timeout_enabled"
      @change="handleAutoSleepToggle"
    >
      <SettingItem :label="t('screenSettings.sleepDelay')">
        <ButtonGroup
          :model-value="config.timeout_seconds"
          :options="sharedDelayPresets"
          mobile-layout="grid-3"
          @change="setScreenTimeout"
        />
      </SettingItem>
    </ToggleSection>
  </SettingsContainer>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue';
import { useI18n } from '@/services/i18n';
import { useSettingsAPI } from '@/composables/useSettingsAPI';
import { useSettingsStore } from '@/stores/settingsStore';
import axios from 'axios';
import ButtonGroup from '@/components/ui/ButtonGroup.vue';
import RangeSlider from '@/components/ui/RangeSlider.vue';
import SettingsContainer from '@/components/settings/SettingsContainer.vue';
import SettingsSection from '@/components/settings/SettingsSection.vue';
import SettingItem from '@/components/settings/SettingItem.vue';
import ToggleSection from '@/components/ui/ToggleSection.vue';

const { t } = useI18n();
const { updateSetting, clearAllTimers } = useSettingsAPI();
const settingsStore = useSettingsStore();
const DEFAULT_DELAY = 30;

// Local refs for instant responsiveness
const config = ref({
  brightness_on: 5,
  timeout_enabled: true,
  timeout_seconds: DEFAULT_DELAY,
  screensaver_enabled: true,
  screensaver_delay_seconds: DEFAULT_DELAY,
  ui_scale: 1.0
});

// Remembers last non-zero timeout for restore on toggle ON
const lastNonZeroTimeout = ref(DEFAULT_DELAY);

// Sync local refs with the store on mount
function syncFromStore() {
  config.value.brightness_on = settingsStore.screenBrightness.brightness_on;
  config.value.timeout_enabled = settingsStore.screenTimeout.screen_timeout_enabled;
  config.value.timeout_seconds = settingsStore.screenTimeout.screen_timeout_seconds;
  config.value.screensaver_enabled = settingsStore.screenScreensaver.screensaver_enabled;
  config.value.screensaver_delay_seconds = settingsStore.screenScreensaver.screensaver_delay_seconds;

  config.value.ui_scale = settingsStore.screenUiScale.ui_scale;

  if (config.value.timeout_seconds > 0) {
    lastNonZeroTimeout.value = config.value.timeout_seconds;
  }
}

const sharedDelayPresets = computed(() => [
  { value: 10, label: t('time.10sec') },
  { value: 30, label: t('time.30sec') },
  { value: 120, label: t('time.2min') },
  { value: 300, label: t('time.5min') },
  { value: 600, label: t('time.10min') },
  { value: 1800, label: t('time.30min') }
]);

const uiScalePresets = [
  { value: 0.9, label: '90%' },
  { value: 0.95, label: '95%' },
  { value: 1.0, label: '100%' },
  { value: 1.05, label: '105%' },
  { value: 1.1, label: '110%' }
];

function setUiScale(value) {
  config.value.ui_scale = value;
  settingsStore.updateScreenUiScale({ ui_scale: value });
  updateSetting('screen-ui-scale', { ui_scale: value });
}

let lastAppliedBrightness = null;
let brightnessThrottleActive = false;
let pendingBrightness = null;

function handleBrightnessChange(value) {
  // Skip if value hasn't changed
  if (value === lastAppliedBrightness) return;

  if (!brightnessThrottleActive) {
    // Fire immediately
    applyBrightness(value);
    brightnessThrottleActive = true;
    setTimeout(() => {
      brightnessThrottleActive = false;
      // Apply any pending value that arrived during throttle window
      if (pendingBrightness !== null && pendingBrightness !== lastAppliedBrightness) {
        applyBrightness(pendingBrightness);
        pendingBrightness = null;
      }
    }, 100);
  } else {
    pendingBrightness = value;
  }
}

function applyBrightness(value) {
  lastAppliedBrightness = value;
  axios.post('/api/settings/screen-brightness/apply', { brightness_on: value }).catch(console.error);
}

function saveBrightness(value) {
  updateSetting('screen-brightness', { brightness_on: value });
}

function handleAutoSleepToggle(enabled) {
  if (enabled) {
    config.value.timeout_enabled = true;
    config.value.timeout_seconds = lastNonZeroTimeout.value;
    updateSetting('screen-timeout', {
      screen_timeout_enabled: true,
      screen_timeout_seconds: lastNonZeroTimeout.value
    });
  } else {
    if (config.value.timeout_seconds > 0) {
      lastNonZeroTimeout.value = config.value.timeout_seconds;
    }
    config.value.timeout_enabled = false;
    config.value.timeout_seconds = 0;
    updateSetting('screen-timeout', {
      screen_timeout_enabled: false,
      screen_timeout_seconds: 0
    });
  }
}

function setScreenTimeout(value) {
  if (value > 0) {
    lastNonZeroTimeout.value = value;
  }
  updateSetting('screen-timeout', {
    screen_timeout_enabled: value !== 0,
    screen_timeout_seconds: value
  });
}

function handleScreensaverToggle(enabled) {
  config.value.screensaver_enabled = enabled;
  if (enabled && !sharedDelayPresets.value.some(p => p.value === config.value.screensaver_delay_seconds)) {
    config.value.screensaver_delay_seconds = DEFAULT_DELAY;
  }
  updateSetting('screen-screensaver', {
    screensaver_enabled: enabled,
    screensaver_delay_seconds: config.value.screensaver_delay_seconds
  });
}

function setScreensaverDelay(value) {
  config.value.screensaver_delay_seconds = value;
  updateSetting('screen-screensaver', { screensaver_delay_seconds: value });
}

// Sync local config when store changes (e.g., WS event from another device)
watch(
  [
    () => settingsStore.screenBrightness,
    () => settingsStore.screenTimeout,
    () => settingsStore.screenScreensaver,
    () => settingsStore.screenUiScale
  ],
  syncFromStore,
  { deep: true }
);

onMounted(() => {
  syncFromStore();
});

onUnmounted(() => {
  clearAllTimers();
});
</script>

<style scoped>
.screensaver-content {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.screensaver-source-note {
  color: var(--color-brand);
}

.screensaver-content :deep(.setting-item) {
  gap: var(--space-04);
}
</style>
