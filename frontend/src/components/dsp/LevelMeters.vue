<!-- frontend/src/components/dsp/LevelMeters.vue -->
<!-- Stereo input/output level meters with real-time monitoring -->
<template>
  <div class="level-meters">
    <!-- Header -->
    <div class="meters-header">
      <span class="meters-title text-mono">{{ $t('dsp.meters.title', 'Audio Levels') }}</span>
    </div>

    <!-- Meters content (always visible) -->
    <div class="meters-content">
      <!-- Input meters -->
      <div class="meter-group">
        <span class="group-label text-mono-small">{{ $t('dsp.meters.input', 'IN') }}</span>
        <div class="stereo-meters">
          <LevelMeter
            :level="inputLeft"
            :min="meterMin"
            :max="meterMax"
            label="L"
            :show-peak="true"
          />
          <LevelMeter
            :level="inputRight"
            :min="meterMin"
            :max="meterMax"
            label="R"
            :show-peak="true"
          />
        </div>
      </div>

      <!-- Output meters -->
      <div class="meter-group">
        <span class="group-label text-mono-small">{{ $t('dsp.meters.output', 'OUT') }}</span>
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
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue';
import { useDspStore } from '@/stores/dspStore';
import { useSettingsStore } from '@/stores/settingsStore';
import LevelMeter from './LevelMeter.vue';
import axios from 'axios';

const dspStore = useDspStore();
const settingsStore = useSettingsStore();

// Dynamic min/max from settings
const meterMin = computed(() => settingsStore.volumeLimits.min_db);
const meterMax = computed(() => settingsStore.volumeLimits.max_db);

let pollInterval = null;

// Convert array levels to individual channels
const inputLeft = computed(() => {
  const levels = dspStore.inputPeak;
  return Array.isArray(levels) && levels.length > 0 ? levels[0] : meterMin.value;
});

const inputRight = computed(() => {
  const levels = dspStore.inputPeak;
  return Array.isArray(levels) && levels.length > 1 ? levels[1] : inputLeft.value;
});

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
    const response = await axios.get('/api/dsp/levels');
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
</script>

<style scoped>
.level-meters {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
  padding: var(--space-03);
  background: var(--color-background-neutral);
  border-radius: var(--radius-05);
}

.meters-header {
  display: flex;
  align-items: center;
}

.meters-title {
  color: var(--color-text-secondary);
}

.meters-content {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

.meter-group {
  display: flex;
  align-items: center;
  gap: var(--space-03);
}

.group-label {
  min-width: 32px;
  color: var(--color-text-light);
  text-align: center;
}

.stereo-meters {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
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

  .meter-group {
    gap: var(--space-02);
  }

  .group-label {
    min-width: 28px;
  }
}
</style>
