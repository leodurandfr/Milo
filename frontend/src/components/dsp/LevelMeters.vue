<!-- frontend/src/components/dsp/LevelMeters.vue -->
<!-- Stereo input/output level meters with real-time monitoring -->
<template>
  <div class="level-meters" :class="{ collapsed: !expanded }">
    <!-- Header with toggle -->
    <button class="meters-header" @click="expanded = !expanded">
      <span class="meters-title">{{ $t('dsp.meters.title', 'Audio Levels') }}</span>
      <SvgIcon :name="expanded ? 'caretUp' : 'caretDown'" :size="16" />
    </button>

    <!-- Meters content -->
    <Transition name="collapse">
      <div v-if="expanded" class="meters-content">
        <!-- Input meters -->
        <div class="meter-group">
          <span class="group-label text-mono">{{ $t('dsp.meters.input', 'IN') }}</span>
          <div class="stereo-meters">
            <LevelMeter
              :level="inputLeft"
              label="L"
              :show-peak="true"
            />
            <LevelMeter
              :level="inputRight"
              label="R"
              :show-peak="true"
            />
          </div>
        </div>

        <!-- Output meters -->
        <div class="meter-group">
          <span class="group-label text-mono">{{ $t('dsp.meters.output', 'OUT') }}</span>
          <div class="stereo-meters">
            <LevelMeter
              :level="outputLeft"
              label="L"
              :show-peak="true"
            />
            <LevelMeter
              :level="outputRight"
              label="R"
              :show-peak="true"
            />
          </div>
        </div>
      </div>
    </Transition>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue';
import { useDspStore } from '@/stores/dspStore';
import LevelMeter from './LevelMeter.vue';
import SvgIcon from '@/components/ui/SvgIcon.vue';
import axios from 'axios';

const dspStore = useDspStore();

const expanded = ref(false);
let pollInterval = null;

// Convert array levels to individual channels
const inputLeft = computed(() => {
  const levels = dspStore.inputPeak;
  return Array.isArray(levels) && levels.length > 0 ? levels[0] : -60;
});

const inputRight = computed(() => {
  const levels = dspStore.inputPeak;
  return Array.isArray(levels) && levels.length > 1 ? levels[1] : inputLeft.value;
});

const outputLeft = computed(() => {
  const levels = dspStore.outputPeak;
  return Array.isArray(levels) && levels.length > 0 ? levels[0] : -60;
});

const outputRight = computed(() => {
  const levels = dspStore.outputPeak;
  return Array.isArray(levels) && levels.length > 1 ? levels[1] : outputLeft.value;
});

// Poll levels from API when expanded
async function pollLevels() {
  if (!expanded.value || !dspStore.isConnected) return;

  try {
    const response = await axios.get('/api/dsp/levels');
    if (response.data.available) {
      dspStore.inputPeak = response.data.input_peak || [-60, -60];
      dspStore.outputPeak = response.data.output_peak || [-60, -60];
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

// Start/stop polling based on expanded state
onMounted(() => {
  if (expanded.value) startPolling();
});

onUnmounted(() => {
  stopPolling();
});

// Watch expanded state
import { watch } from 'vue';
watch(expanded, (isExpanded) => {
  if (isExpanded) {
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
  background: var(--color-background-neutral);
  border-radius: var(--radius-05);
  overflow: hidden;
}

.meters-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-02) var(--space-03);
  background: transparent;
  border: none;
  color: var(--color-text-secondary);
  cursor: pointer;
  transition: all var(--transition-fast);
}

.meters-header:hover {
  background: var(--color-border);
}

.meters-title {
  font-size: 13px;
  font-weight: 500;
}

.meters-content {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
  padding: var(--space-03);
  padding-top: 0;
}

.meter-group {
  display: flex;
  align-items: center;
  gap: var(--space-03);
}

.group-label {
  min-width: 32px;
  font-size: 11px;
  color: var(--color-text-light);
  text-align: center;
}

.stereo-meters {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
}

/* Collapse transition */
.collapse-enter-active,
.collapse-leave-active {
  transition: all var(--transition-normal);
  overflow: hidden;
}

.collapse-enter-from,
.collapse-leave-to {
  opacity: 0;
  max-height: 0;
  padding-top: 0;
  padding-bottom: 0;
}

.collapse-enter-to,
.collapse-leave-from {
  max-height: 200px;
}
</style>
