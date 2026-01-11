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
import LevelMeter from './LevelMeter.vue';
import axios from 'axios';

const props = defineProps({
  clientIds: {
    type: Array,
    default: () => ['local']
  }
});

const dspStore = useDspStore();
const settingsStore = useSettingsStore();

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

// Poll levels from API
async function pollLevels() {
  if (!dspStore.isConnected) return;

  try {
    // Use zone endpoint for multiple clients, local endpoint for single client
    const ids = props.clientIds;
    const endpoint = ids.length > 1
      ? `/api/dsp/levels/zone/${ids.join(',')}`
      : '/api/dsp/levels';

    const response = await axios.get(endpoint);
    if (response.data.available) {
      dspStore.inputPeak = response.data.input_peak || [meterMin.value, meterMin.value];
      dspStore.outputPeak = response.data.output_peak || [meterMin.value, meterMin.value];
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

// Start polling when component mounts (always visible now)
onMounted(() => {
  startPolling();
});

onUnmounted(() => {
  stopPolling();
});

// Watch connection state
watch(() => dspStore.isConnected, (isConnected) => {
  if (isConnected) {
    startPolling();
  } else {
    stopPolling();
  }
});

// Re-poll immediately when clientIds change (zone selection changed)
watch(() => props.clientIds, () => {
  if (dspStore.isConnected) {
    pollLevels();
  }
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
  .level-meters {
    padding: var(--space-02);
    gap: var(--space-01);
  }

  .meters-content {
    gap: var(--space-02);
  }
}
</style>
