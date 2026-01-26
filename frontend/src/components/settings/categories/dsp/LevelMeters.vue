<!-- frontend/src/components/settings/categories/dsp/LevelMeters.vue -->
<!-- Stereo input/output level meters with real-time monitoring -->
<template>
  <div class="level-meters">
    <!-- Header -->
    <div class="meters-header">
      <h2 class="heading-2">{{ $t('dsp.meters.title', 'Niveaux de sortie audio') }}</h2>
    </div>

    <!-- Meters content (always visible) -->
    <div class="meters-content">
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
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue';
import { useDspStore } from '@/stores/dspStore';
import { useSettingsStore } from '@/stores/settingsStore';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import LevelMeter from './LevelMeter.vue';
import axios from 'axios';

const props = defineProps({
  clientIds: {
    type: Array,
    default: () => []  // Empty until registry loads and auto-selects
  }
});

const dspStore = useDspStore();
const settingsStore = useSettingsStore();
const audioStore = useUnifiedAudioStore();

// Dynamic min/max from settings
const meterMin = computed(() => settingsStore.volumeLimits.min_db);
const meterMax = computed(() => settingsStore.volumeLimits.max_db);

let pollInterval = null;

// Convert array levels to individual channels
const outputLeft = computed(() => {
  const levels = dspStore.outputPeak;
  return Array.isArray(levels) && levels.length > 0 ? levels[0] : meterMin.value;
});

const outputRight = computed(() => {
  const levels = dspStore.outputPeak;
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

  // All clients muted - show no levels
  if (ids.length === 0) {
    dspStore.inputPeak = [meterMin.value, meterMin.value];
    dspStore.outputPeak = [meterMin.value, meterMin.value];
    return;
  }

  try {
    // Always use zone endpoint - it handles both local and remote clients correctly
    const endpoint = `/api/dsp/levels/zone/${ids.join(',')}`;

    const response = await axios.get(endpoint);
    if (response.data.available) {
      dspStore.inputPeak = response.data.input_peak || [meterMin.value, meterMin.value];
      dspStore.outputPeak = response.data.output_peak || [meterMin.value, meterMin.value];
    } else {
      // No clients available - reset to minimum
      dspStore.inputPeak = [meterMin.value, meterMin.value];
      dspStore.outputPeak = [meterMin.value, meterMin.value];
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
.level-meters {
  display: flex;
  flex-direction: column;
  gap: var(--space-05);
  padding: var(--space-05-fixed) var(--space-05);
  background: var(--color-background-neutral);
  border-radius: var(--radius-05);
}

.meters-header {
  display: flex;
  align-items: center;
}


.meters-content {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

.stereo-meters {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

/* Mobile adjustments */
@media (max-aspect-ratio: 4/3) {
}
</style>
