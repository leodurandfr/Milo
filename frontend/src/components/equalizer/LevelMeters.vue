<!-- frontend/src/components/equalizer/LevelMeters.vue -->
<!-- Stereo input/output level meters with real-time monitoring -->
<template>
  <SettingsSection :title="t('equalizer.meters.title')">
    <!-- Output meters -->
    <div class="stereo-meters">
      <LevelMeter
        :level="outputLeft"
        :min="meterMin"
        :max="meterMax"
        label="L"
        :show-peak="true"
      />
      <LevelMeter
        :level="outputRight"
        :min="meterMin"
        :max="meterMax"
        label="R"
        :show-peak="true"
      />
    </div>
  </SettingsSection>
</template>

<script setup>
import { computed, onMounted, onUnmounted, watch } from 'vue';
import { useI18n } from '@/services/i18n';
import { useEqualizerStore } from '@/stores/equalizerStore';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import SettingsSection from '@/components/settings/SettingsSection.vue';
import LevelMeter from './LevelMeter.vue';
import axios from 'axios';

const props = defineProps({
  clientIds: {
    type: Array,
    default: () => []  // Empty until registry loads and auto-selects
  }
});

const { t } = useI18n();
const equalizerStore = useEqualizerStore();
const audioStore = useUnifiedAudioStore();

// Fixed metering range: 0 dBFS is the standard reference for audio level meters,
// regardless of volume control settings (which define the volume knob range, not signal range)
const meterMin = -60;
const meterMax = 0;

let pollInterval = null;

// Convert array levels to individual channels
const outputLeft = computed(() => {
  const levels = equalizerStore.outputPeak;
  return Array.isArray(levels) && levels.length > 0 ? levels[0] : meterMin;
});

const outputRight = computed(() => {
  const levels = equalizerStore.outputPeak;
  return Array.isArray(levels) && levels.length > 1 ? levels[1] : outputLeft.value;
});

// Filter out muted clients - only get levels from clients that can produce audio
const activeClientIds = computed(() => {
  return props.clientIds.filter(id => {
    const clientState = audioStore.volumeState.clients[id];
    // Include client if we don't have state yet (assume not muted) or if not muted
    return !clientState || !clientState.mute;
  });
});

// Poll levels from API
async function pollLevels() {
  const ids = activeClientIds.value;
  const silent = [meterMin, meterMin];

  try {
    // Use zone endpoint when clients are known, otherwise direct local endpoint
    const endpoint = ids.length > 0
      ? `/api/equalizer/levels/zone/${ids.join(',')}`
      : '/api/equalizer/levels';

    const response = await axios.get(endpoint);
    if (response.data.available) {
      equalizerStore.updateLevels(
        response.data.input_peak || silent,
        response.data.output_peak || silent
      );
    } else {
      equalizerStore.updateLevels(silent, silent);
    }
  } catch (error) {
    // Silently fail - levels are optional
  }
}

function startPolling() {
  if (pollInterval) return;
  pollLevels();
  pollInterval = setInterval(pollLevels, 100); // 10Hz update rate
}

function stopPolling() {
  if (pollInterval) {
    clearInterval(pollInterval);
    pollInterval = null;
  }
}

// Start polling when component mounts
onMounted(() => {
  startPolling();
});

onUnmounted(() => {
  stopPolling();
});

// Re-poll immediately when clientIds or mute states change
watch([() => props.clientIds, activeClientIds], () => {
  pollLevels();
}, { deep: true });
</script>

<style scoped>
.stereo-meters {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}
</style>
