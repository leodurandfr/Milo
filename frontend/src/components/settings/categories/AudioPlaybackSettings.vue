<!-- frontend/src/components/settings/categories/AudioPlaybackSettings.vue -->
<template>
  <SectionStack>
    <!-- Auto-stop on pause (applies to every eligible source) -->
    <ToggleSection
      :title="t('audioPlayback.autoStop')"
      :enabled="autoStopEnabled"
      @change="toggleAutoStop"
    >
      <RangeSlider :label="t('audioPlayback.autoStopHint')"
        :model-value="autoStopShown"
        :steps="autoStopSteps"
        @change="setAutoStop"
      />
    </ToggleSection>

    <!-- What a source left behind, shown again when it is selected back in time -->
    <ToggleSection
      :title="t('audioPlayback.resume')"
      :enabled="resumeEnabled"
      @change="toggleResume"
    >
      <RangeSlider :label="t('audioPlayback.resumeHint')"
        :model-value="resumeShown ?? FOREVER"
        :steps="resumeSteps"
        @change="(value) => setResume(value === FOREVER ? null : value)"
      />
    </ToggleSection>
  </SectionStack>
</template>

<script setup>
import { ref, computed, watch } from 'vue';
import { useI18n } from '@/services/i18n';
import { useSettingsAPI } from '@/composables/useSettingsAPI';
import { useSettingsStore } from '@/stores/settingsStore';
import RangeSlider from '@/components/ui/RangeSlider.vue';
import SectionStack from '@/components/ui/SectionStack.vue';
import ToggleSection from '@/components/ui/ToggleSection.vue';

const { t, formatDuration } = useI18n();
const { updateSetting } = useSettingsAPI();
const settingsStore = useSettingsStore();

// The resume window's last stop: stored as null (no limit), which a slider
// cannot hold. Past every duration, so a stored value off the grid snaps to the
// nearest duration (RangeSlider) and never to "forever".
const FOREVER = Number.MAX_SAFE_INTEGER;

/**
 * One section of this panel: a switch whose off is stored as 0, over a slider
 * of durations. Off keeps the last value, so switching back on restores it
 * and the section collapses on it rather than on the first stop.
 *
 * @param {string} key - the setting in settingsStore.audioPlayback
 * @param {string} endpoint - its PUT /api/settings/<endpoint>
 * @param {number} onValue - what switching on picks when the setting was
 *   already off as the panel opened, so there is no last value to restore
 */
function offableSetting(key, endpoint, onValue) {
  const stored = settingsStore.audioPlayback[key];
  const value = ref(stored);
  const last = ref(stored !== 0 ? stored : onValue);
  const enabled = computed(() => value.value !== 0);
  const shown = computed(() => (enabled.value ? value.value : last.value));

  function sync(stored) {
    value.value = stored;
    if (stored !== 0) last.value = stored;
  }

  function set(next) {
    sync(next);
    settingsStore.updateAudioPlayback({ [key]: next });
    updateSetting(endpoint, { [key]: next });
  }

  function toggle(on) {
    set(on ? last.value : 0);
  }

  watch(() => settingsStore.audioPlayback[key], sync);

  return { enabled, shown, set, toggle };
}

const {
  enabled: autoStopEnabled, shown: autoStopShown, set: setAutoStop, toggle: toggleAutoStop
} = offableSetting('auto_stop_delay', 'audio-stop', 120);
const {
  enabled: resumeEnabled, shown: resumeShown, set: setResume, toggle: toggleResume
} = offableSetting('resume_window', 'audio-resume', 600);

const durationSteps = (values) => values.map(value => ({ value, label: formatDuration(value) }));

const autoStopSteps = computed(() => durationSteps([30, 60, 120, 300, 600, 1200, 1800, 3600, 7200]));

const resumeSteps = computed(() => [
  ...durationSteps([15, 30, 60, 120, 300, 600, 1800, 3600, 7200, 21600, 86400]),
  { value: FOREVER, label: t('audioPlayback.resumeForever') },
]);
</script>
