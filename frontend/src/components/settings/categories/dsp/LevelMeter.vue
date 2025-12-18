<!-- frontend/src/components/settings/categories/dsp/LevelMeter.vue -->
<!-- Audio level meter with peak hold -->
<template>
  <div class="level-meter" :class="{ vertical: orientation === 'vertical' }">
    <div v-if="label" class="meter-label text-mono">{{ label }}</div>

    <div class="meter-container">
      <div class="meter-track">
        <!-- Level bar -->
        <div
          class="meter-bar"
          :class="{ warning: level > -6, danger: level > -3 }"
          :style="{ [orientation === 'vertical' ? 'height' : 'width']: levelPercent + '%' }"
        ></div>

        <!-- Peak hold indicator -->
        <div
          v-if="showPeak"
          class="peak-indicator"
          :class="{ warning: peakLevel > -6, danger: peakLevel > -3 }"
          :style="{ [orientation === 'vertical' ? 'bottom' : 'left']: peakPercent + '%' }"
        ></div>
      </div>

      <!-- Scale markers (dynamic based on min/max) -->
      <div v-if="showScale" class="meter-scale">
        <span
          v-for="marker in scaleMarkers"
          :key="marker.value"
          class="scale-marker"
          :style="{ '--pos': marker.position + '%' }"
        >{{ marker.label }}</span>
      </div>
    </div>

    <!-- Value display -->
    <div v-if="showValue" class="meter-value text-mono">
      {{ level.toFixed(1) }} dB
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onUnmounted } from 'vue';

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
  orientation: {
    type: String,
    default: 'horizontal',
    validator: (v) => ['horizontal', 'vertical'].includes(v)
  },
  showPeak: {
    type: Boolean,
    default: true
  },
  showScale: {
    type: Boolean,
    default: false
  },
  showValue: {
    type: Boolean,
    default: false
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
const peakLevel = ref(props.level);
let peakHoldTimer = null;
let decayInterval = null;

// Convert dB to percentage (0-100)
function dbToPercent(db) {
  const clampedDb = Math.max(props.min, Math.min(props.max, db));
  return ((clampedDb - props.min) / (props.max - props.min)) * 100;
}

const levelPercent = computed(() => dbToPercent(props.level));
const peakPercent = computed(() => dbToPercent(peakLevel.value));

// Dynamic scale markers based on min/max
const scaleMarkers = computed(() => {
  const range = props.max - props.min;
  // Generate 5 markers at 0%, 25%, 50%, 75%, 100%
  return [
    { value: props.max, position: 100, label: props.max },
    { value: props.min + range * 0.75, position: 75, label: Math.round(props.min + range * 0.75) },
    { value: props.min + range * 0.5, position: 50, label: Math.round(props.min + range * 0.5) },
    { value: props.min + range * 0.25, position: 25, label: Math.round(props.min + range * 0.25) },
    { value: props.min, position: 0, label: props.min }
  ];
});

// Update peak level
watch(() => props.level, (newLevel) => {
  if (newLevel > peakLevel.value) {
    peakLevel.value = newLevel;

    // Clear existing timers
    if (peakHoldTimer) clearTimeout(peakHoldTimer);
    if (decayInterval) clearInterval(decayInterval);

    // Start decay after hold time
    peakHoldTimer = setTimeout(() => {
      decayInterval = setInterval(() => {
        peakLevel.value = Math.max(props.level, peakLevel.value - props.peakDecay);

        if (peakLevel.value <= props.level) {
          clearInterval(decayInterval);
          decayInterval = null;
        }
      }, 16); // ~60fps
    }, props.peakHoldTime);
  }
});

onUnmounted(() => {
  if (peakHoldTimer) clearTimeout(peakHoldTimer);
  if (decayInterval) clearInterval(decayInterval);
});
</script>

<style scoped>
.level-meter {
  display: flex;
  align-items: center;
  gap: var(--space-02);
  width: 100%;
}

.level-meter.vertical {
  flex-direction: column;
  width: auto;
  height: 100%;
}

.meter-label {
  min-width: 24px;
  font-size: 11px;
  color: var(--color-text-secondary);
  text-align: center;
}

.meter-container {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.level-meter.vertical .meter-container {
  flex-direction: row;
  height: 100%;
  width: auto;
}

.meter-track {
  position: relative;
  height: 8px;
  background: var(--color-background);
  border-radius: 4px;
  overflow: hidden;
}

.level-meter.vertical .meter-track {
  height: 100%;
  width: 8px;
}

.meter-bar {
  position: absolute;
  bottom: 0;
  left: 0;
  height: 100%;
  background: var(--color-success);
  border-radius: 4px;
  transition: width 50ms linear, height 50ms linear;
}

.level-meter.vertical .meter-bar {
  width: 100%;
  height: 0;
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
  transition: left 50ms linear, bottom 50ms linear;
}

.level-meter.vertical .peak-indicator {
  width: 100%;
  height: 2px;
}

.peak-indicator.warning {
  background: var(--color-warning);
}

.peak-indicator.danger {
  background: var(--color-error);
}

.meter-scale {
  display: flex;
  justify-content: space-between;
  position: relative;
  height: 12px;
}

.level-meter.vertical .meter-scale {
  flex-direction: column-reverse;
  width: 12px;
  height: 100%;
}

.scale-marker {
  position: absolute;
  left: var(--pos);
  font-size: 9px;
  color: var(--color-text-light);
  transform: translateX(-50%);
}

.level-meter.vertical .scale-marker {
  left: auto;
  bottom: var(--pos);
  transform: translateY(50%);
}

.meter-value {
  min-width: 56px;
  font-size: 11px;
  color: var(--color-text-secondary);
  text-align: right;
}
</style>
