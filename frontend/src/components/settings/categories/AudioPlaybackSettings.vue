<!-- frontend/src/components/settings/categories/AudioPlaybackSettings.vue -->
<template>
  <SettingsContainer>
    <!-- Auto-stop on pause (applies to every eligible source) -->
    <ToggleSection
      :title="t('audioPlayback.autoStop')"
      :enabled="autoStopEnabled"
      @change="handleAutoStopToggle"
    >
      <SettingItem :label="t('audioPlayback.autoStopHint')">
        <!-- Off is stored as 0; the section collapses on the last delay, not the first stop -->
        <RangeSlider
          :model-value="autoStopEnabled ? config.auto_stop_delay : lastAutoStop"
          :steps="autoStopSteps"
          @change="setAutoStopDelay"
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
import RangeSlider from '@/components/ui/RangeSlider.vue';
import SettingsContainer from '@/components/settings/SettingsContainer.vue';
import SettingItem from '@/components/settings/SettingItem.vue';
import ToggleSection from '@/components/ui/ToggleSection.vue';

const { t } = useI18n();
const { updateSetting } = useSettingsAPI();
const settingsStore = useSettingsStore();

// Local state for instant responsiveness
const config = ref({
  auto_stop_delay: 120
});

// Remember last non-zero value so toggling OFF then ON restores the user choice
const lastAutoStop = ref(120);

const autoStopEnabled = computed(() => config.value.auto_stop_delay !== 0);

const autoStopSteps = computed(() => [
  { value: 30, label: t('time.30sec') },
  { value: 60, label: t('time.1min') },
  { value: 120, label: t('time.2min') },
  { value: 300, label: t('time.5min') },
  { value: 600, label: t('time.10min') },
  { value: 1200, label: t('time.20min') },
  { value: 1800, label: t('time.30min') },
  { value: 3600, label: t('time.1h') },
  { value: 7200, label: t('time.2h') }
]);

function syncFromStore() {
  const playback = settingsStore.audioPlayback;
  config.value.auto_stop_delay = playback.auto_stop_delay;
  if (playback.auto_stop_delay > 0) {
    lastAutoStop.value = playback.auto_stop_delay;
  }
}

function handleAutoStopToggle(enabled) {
  const next = enabled ? lastAutoStop.value : 0;
  if (!enabled && config.value.auto_stop_delay > 0) {
    lastAutoStop.value = config.value.auto_stop_delay;
  }
  config.value.auto_stop_delay = next;
  settingsStore.updateAudioPlayback({ auto_stop_delay: next });
  updateSetting('audio-stop', { auto_stop_delay: next });
}

function setAutoStopDelay(value) {
  if (value > 0) {
    lastAutoStop.value = value;
  }
  config.value.auto_stop_delay = value;
  settingsStore.updateAudioPlayback({ auto_stop_delay: value });
  updateSetting('audio-stop', { auto_stop_delay: value });
}

// Re-sync when the store changes (e.g. WS event from another device)
watch(() => settingsStore.audioPlayback, syncFromStore, { deep: true });

onMounted(() => {
  syncFromStore();
});
</script>
