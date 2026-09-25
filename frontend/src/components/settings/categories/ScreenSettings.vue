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
          mobile-layout="grid-3"
          @change="setUiScale"
        />
      </SettingItem>
    </SettingsSection>

    <!-- Warm color filter (Night Shift-like) -->
    <ToggleSection
      :title="t('screenSettings.colorFilter')"
      :enabled="config.color_filter_enabled"
      @change="handleColorFilterToggle"
    >
      <SettingItem :label="t('screenSettings.colorFilterWarmth')">
        <RangeSlider
          v-model="config.color_filter_warmth"
          :min="0"
          :max="100"
          :step="1"
          value-unit="%"
          @input="previewColorFilterWarmth"
          @change="saveColorFilterWarmth"
        />
      </SettingItem>
    </ToggleSection>

    <!-- Screensaver -->
    <ToggleSection
      :title="t('screenSettings.screensaver')"
      :enabled="config.screensaver_enabled"
      @change="handleScreensaverToggle"
    >
      <div class="screensaver-content">
        <SettingItem :label="t('screenSettings.screensaverDelay')">
          <RangeSlider
            :model-value="config.screensaver_delay_seconds"
            :steps="delaySteps"
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
        <!-- Off is stored as 0; the section collapses on the last delay, not the first stop -->
        <RangeSlider
          :model-value="config.timeout_enabled ? config.timeout_seconds : lastNonZeroTimeout"
          :steps="delaySteps"
          @change="setScreenTimeout"
        />
      </SettingItem>
    </ToggleSection>
  </SettingsContainer>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue';
import { useI18n } from '@/services/i18n';
import { useSettingsAPI } from '@/composables/useSettingsAPI';
import { useSettingsStore } from '@/stores/settingsStore';
import { useTimer } from '@/composables/useTimer';
import { apiCall } from '@/services/apiCall';
import ButtonGroup from '@/components/ui/ButtonGroup.vue';
import RangeSlider from '@/components/ui/RangeSlider.vue';
import SettingsContainer from '@/components/settings/SettingsContainer.vue';
import SettingsSection from '@/components/settings/SettingsSection.vue';
import SettingItem from '@/components/settings/SettingItem.vue';
import ToggleSection from '@/components/ui/ToggleSection.vue';

const { t } = useI18n();
const { updateSetting } = useSettingsAPI();
const timer = useTimer();
const settingsStore = useSettingsStore();
const DEFAULT_DELAY = 30;

// Local refs for instant responsiveness
const config = ref({
  brightness_on: 5,
  timeout_enabled: true,
  timeout_seconds: DEFAULT_DELAY,
  screensaver_enabled: true,
  screensaver_delay_seconds: DEFAULT_DELAY,
  ui_scale: 1.0,
  color_filter_enabled: false,
  color_filter_warmth: 50
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

  config.value.color_filter_enabled = settingsStore.screenColorFilter.enabled;
  config.value.color_filter_warmth = settingsStore.screenColorFilter.warmth;

  if (config.value.timeout_seconds > 0) {
    lastNonZeroTimeout.value = config.value.timeout_seconds;
  }
}

const delaySteps = computed(() => [
  { value: 10, label: t('time.10sec') },
  { value: 20, label: t('time.20sec') },
  { value: 30, label: t('time.30sec') },
  { value: 60, label: t('time.1min') },
  { value: 120, label: t('time.2min') },
  { value: 300, label: t('time.5min') },
  { value: 600, label: t('time.10min') },
  { value: 1200, label: t('time.20min') },
  { value: 1800, label: t('time.30min') },
  { value: 3600, label: t('time.1h') }
]);

const uiScalePresets = [
  { value: 0.9, label: '90%' },
  { value: 0.95, label: '95%' },
  { value: 1.0, label: '100%' },
  { value: 1.05, label: '105%' },
  { value: 1.1, label: '110%' },
  { value: 1.15, label: '115%' }
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
  if (value === lastAppliedBrightness) return;

  if (!brightnessThrottleActive) {
    // Fire immediately
    applyBrightness(value);
    brightnessThrottleActive = true;
    timer.setTimeout(() => {
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
  apiCall.post('/api/settings/screen-brightness/apply', { brightness_on: value }, {
    category: 'screen',
    message: 'Failed to apply brightness'
  });
}

function saveBrightness(value) {
  updateSetting('screen-brightness', { brightness_on: value });
}

// Local config, store and backend together: the store is what syncFromStore
// reads back when any other screen setting moves before the WS echo lands.
function commitTimeout(seconds) {
  config.value.timeout_enabled = seconds !== 0;
  config.value.timeout_seconds = seconds;
  const payload = { screen_timeout_enabled: seconds !== 0, screen_timeout_seconds: seconds };
  settingsStore.updateScreenTimeout(payload);
  updateSetting('screen-timeout', payload);
}

function commitScreensaver(payload) {
  Object.assign(config.value, payload);
  settingsStore.updateScreenScreensaver(payload);
  updateSetting('screen-screensaver', payload);
}

function handleAutoSleepToggle(enabled) {
  if (!enabled && config.value.timeout_seconds > 0) {
    lastNonZeroTimeout.value = config.value.timeout_seconds;
  }
  commitTimeout(enabled ? lastNonZeroTimeout.value : 0);
}

function setScreenTimeout(value) {
  lastNonZeroTimeout.value = value;
  commitTimeout(value);
}

function handleScreensaverToggle(enabled) {
  commitScreensaver({ screensaver_enabled: enabled });
}

function setScreensaverDelay(value) {
  commitScreensaver({ screensaver_delay_seconds: value });
}

function handleColorFilterToggle(enabled) {
  config.value.color_filter_enabled = enabled;
  settingsStore.updateScreenColorFilter({ enabled });
  updateSetting('screen-color-filter', { enabled });
}

function previewColorFilterWarmth(value) {
  settingsStore.updateScreenColorFilter({ warmth: value });
}

function saveColorFilterWarmth(value) {
  config.value.color_filter_warmth = value;
  settingsStore.updateScreenColorFilter({ warmth: value });
  updateSetting('screen-color-filter', { warmth: value });
}

// Sync local config when store changes (e.g., WS event from another device)
watch(
  [
    () => settingsStore.screenBrightness,
    () => settingsStore.screenTimeout,
    () => settingsStore.screenScreensaver,
    () => settingsStore.screenUiScale,
    () => settingsStore.screenColorFilter
  ],
  syncFromStore,
  { deep: true }
);

onMounted(() => {
  syncFromStore();
});
</script>

<style scoped>
.screensaver-content {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.screensaver-content :deep(.setting-item) {
  gap: var(--space-04);
}
</style>
