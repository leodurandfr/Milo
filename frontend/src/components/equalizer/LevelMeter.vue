<!-- frontend/src/components/equalizer/LevelMeter.vue -->
<!-- Audio level meter with peak hold -->
<template>
  <div class="level-meter">
    <div v-if="label" class="meter-label text-mono-medium">{{ label }}</div>

    <div class="meter-container">
      <div class="meter-track">
        <div
          class="meter-bar"
          :class="{ warning: level > -6, danger: level > -3 }"
          :style="{ width: levelPercent + '%' }"
        ></div>

        <div
          v-if="showPeak"
          class="peak-indicator"
          :class="{ warning: peakLevel > -6, danger: peakLevel > -3 }"
          :style="{ left: peakPercent + '%' }"
        ></div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue';
import { dbToPercent } from '@/constants/volumeConversion';
import { useTimer } from '@/composables/useTimer';

const props = defineProps({
  level: {
    type: Number,
    default: -80
  },
  min: {
    type: Number,
    default: -80
  },
  max: {
    type: Number,
    default: 0
  },
  label: {
    type: String,
    default: ''
  },
  showPeak: {
    type: Boolean,
    default: true
  },
  peakHoldTime: {
    type: Number,
    default: 2000 // ms
  },
  peakDecay: {
    type: Number,
    default: 0.5 // dB per frame
  }
});

// Peak hold logic
const timer = useTimer();
const peakLevel = ref(props.level);
let peakHoldTimer = null;
let decayInterval = null;

const levelPercent = computed(() => dbToPercent(props.level, props.min, props.max));
const peakPercent = computed(() => dbToPercent(peakLevel.value, props.min, props.max));

// Update peak level
watch(() => props.level, (newLevel) => {
  if (newLevel > peakLevel.value) {
    peakLevel.value = newLevel;

    if (peakHoldTimer) timer.clear(peakHoldTimer);
    if (decayInterval) timer.clear(decayInterval);

    // Start decay after hold time
    peakHoldTimer = timer.setTimeout(() => {
      decayInterval = timer.setInterval(() => {
        peakLevel.value = Math.max(props.level, peakLevel.value - props.peakDecay);

        if (peakLevel.value <= props.level) {
          timer.clear(decayInterval);
          decayInterval = null;
        }
      }, 16); // ~60fps
    }, props.peakHoldTime);
  }
});
</script>

<style scoped>
.level-meter {
  display: flex;
  align-items: center;
  gap: var(--space-02);
  width: 100%;
}

.meter-label {
  min-width: 24px;
  color: var(--color-text-secondary);
  text-align: center;
}

.meter-container {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.meter-track {
  position: relative;
  height: 4px;
  background: var(--color-background);
  border-radius: var(--radius-full);
  overflow: hidden;
}

.meter-bar {
  position: absolute;
  bottom: 0;
  left: 0;
  height: 100%;
  background: var(--color-brand);
  border-radius: var(--radius-01);
  transition: width 100ms linear;
}

.meter-bar.warning {
  background: var(--color-warning);
}

.meter-bar.danger {
  background: var(--color-error);
}

.peak-indicator {
  position: absolute;
  width: 2px;
  height: 100%;
  background: var(--color-text-secondary);
  transition: left 100ms linear;
}

.peak-indicator.warning {
  background: var(--color-warning);
}

.peak-indicator.danger {
  background: var(--color-error);
}
</style>
