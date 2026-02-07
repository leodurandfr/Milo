<!-- frontend/src/components/ui/RangeSlider.vue -->
<template>
  <div :class="['slider-container', orientation, { disabled, muted }]" :style="cssVars">
    <div ref="track" class="range-track"></div>

    <div
      class="range-thumb"
      :class="{ dragging: isDragging }"
      :style="thumbStyle"
      @pointerdown="startDrag"
    ></div>

    <div v-if="orientation === 'horizontal' && !hideInlineValue" class="slider-value text-mono" :class="{ dragging: isDragging, muted: muted }">
      {{ effectiveValue }}{{ valueUnit }}
    </div>
  </div>
</template>

<script setup>
import { computed, ref, onMounted, onUnmounted } from 'vue';

const props = defineProps({
  modelValue: { type: Number, required: true },
  min: { type: Number, default: 0 },
  max: { type: Number, default: 100 },
  step: { type: Number, default: 1 },
  orientation: { type: String, default: 'horizontal' },
  disabled: { type: Boolean, default: false },
  muted: { type: Boolean, default: false },
  valueUnit: { type: String, default: '' },
  hideInlineValue: { type: Boolean, default: false }
});

const emit = defineEmits(['update:modelValue', 'input', 'change', 'drag-start', 'drag-end']);

const isDragging = ref(false);
const track = ref(null);
const trackSize = ref({ width: 0, height: 0 });

// Local value during drag - prevents external updates (WebSocket echo) from causing jumps
const localDragValue = ref(null);

let resizeObserver = null;
let thumbOffset = 0;

// Effective value: local during drag, prop otherwise
const effectiveValue = computed(() => {
  return localDragValue.value !== null ? localDragValue.value : props.modelValue;
});

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function roundToStep(value) {
  return parseFloat((Math.round(value / props.step) * props.step).toFixed(10));
}

// Thumb positioning via CSS calc (same formula as DoubleRangeSlider)
const thumbStyle = computed(() => {
  const pct = (effectiveValue.value - props.min) / (props.max - props.min);
  if (props.orientation === 'horizontal') {
    return { left: `calc(31px + ${pct} * (100% - 62px))` };
  } else {
    return { bottom: `calc(31px + ${pct} * (100% - 62px))` };
  }
});

// Progress percentage for CSS gradient (accounts for thumb size)
const percentage = computed(() => {
  const rawPercentage = ((effectiveValue.value - props.min) / (props.max - props.min)) * 100;

  if (props.orientation === 'horizontal') {
    const thumbWidth = 62;
    const containerWidth = trackSize.value.width || 400;
    const thumbAdjustment = (thumbWidth / containerWidth) * 100;
    return rawPercentage * (100 - thumbAdjustment) / 100 + thumbAdjustment / 2;
  } else {
    const thumbHeight = 62;
    const containerHeight = trackSize.value.height || 260;
    const thumbAdjustment = (thumbHeight / containerHeight) * 100;
    return rawPercentage * (100 - thumbAdjustment) / 100 + thumbAdjustment / 2;
  }
});

const cssVars = computed(() => ({
  '--progress': `${percentage.value}%`
}));

// Drag handling with offset to prevent thumb jump
function startDrag(event) {
  if (event.button !== 0 || props.disabled) return;

  event.preventDefault();
  event.stopPropagation();

  if (!track.value) return;

  const rect = track.value.getBoundingClientRect();
  const currentPct = (props.modelValue - props.min) / (props.max - props.min);

  if (props.orientation === 'horizontal') {
    const usableWidth = rect.width - 62;
    const thumbCenterX = rect.left + 31 + (currentPct * usableWidth);
    thumbOffset = event.clientX - thumbCenterX;
  } else {
    const usableHeight = rect.height - 62;
    const thumbCenterY = rect.bottom - 31 - (currentPct * usableHeight);
    thumbOffset = event.clientY - thumbCenterY;
  }

  localDragValue.value = props.modelValue;
  isDragging.value = true;
  emit('drag-start');

  document.addEventListener('pointermove', handleDrag);
  document.addEventListener('pointerup', stopDrag);
  document.addEventListener('pointercancel', stopDrag);
}

function handleDrag(event) {
  if (!track.value) return;

  const rect = track.value.getBoundingClientRect();
  let pct;

  if (props.orientation === 'horizontal') {
    const correctedX = event.clientX - thumbOffset;
    const usableWidth = rect.width - 62;
    const positionInUsableArea = correctedX - rect.left - 31;
    pct = clamp(positionInUsableArea / usableWidth, 0, 1);
  } else {
    const correctedY = event.clientY - thumbOffset;
    const usableHeight = rect.height - 62;
    const positionInUsableArea = rect.bottom - 31 - correctedY;
    pct = clamp(positionInUsableArea / usableHeight, 0, 1);
  }

  const rawValue = props.min + pct * (props.max - props.min);
  const value = clamp(roundToStep(rawValue), props.min, props.max);

  localDragValue.value = value;
  emit('update:modelValue', value);
  emit('input', value);
}

function stopDrag() {
  if (isDragging.value) {
    isDragging.value = false;
    emit('change', effectiveValue.value);
    emit('drag-end');
    localDragValue.value = null;
  }

  document.removeEventListener('pointermove', handleDrag);
  document.removeEventListener('pointerup', stopDrag);
  document.removeEventListener('pointercancel', stopDrag);
}

function updateTrackSize() {
  if (track.value) {
    const rect = track.value.getBoundingClientRect();
    trackSize.value = { width: rect.width, height: rect.height };
  }
}

onMounted(() => {
  if (track.value) {
    updateTrackSize();
    resizeObserver = new ResizeObserver(() => {
      updateTrackSize();
    });
    resizeObserver.observe(track.value);
  }
});

onUnmounted(() => {
  document.removeEventListener('pointermove', handleDrag);
  document.removeEventListener('pointerup', stopDrag);
  document.removeEventListener('pointercancel', stopDrag);

  if (resizeObserver) {
    resizeObserver.disconnect();
  }
});
</script>

<style>
@property --slider-accent {
  syntax: '<color>';
  inherits: true;
  initial-value: transparent;
}
</style>

<style scoped>
.slider-container {
  --slider-accent: var(--color-text-secondary);
  transition: --slider-accent var(--transition-fast);
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
}

.slider-container.horizontal {
  width: 100%;
  height: 40px;
}

.slider-container.vertical {
  width: 40px;
  flex: 1;
  flex-direction: column;
}

/* Track */
.range-track {
  border-radius: 20px;
  pointer-events: none;
}

.slider-container.horizontal .range-track {
  width: 100%;
  height: 40px;
  background: linear-gradient(to right,
      var(--slider-accent) 0%,
      var(--slider-accent) var(--progress),
      var(--color-background-strong) var(--progress),
      var(--color-background-strong) 100%);
}

.slider-container.vertical .range-track {
  width: 40px;
  min-height: 260px;
  flex: 1;
  background: linear-gradient(to top,
      var(--slider-accent) 0%,
      var(--slider-accent) var(--progress),
      var(--color-background-strong) var(--progress),
      var(--color-background-strong) 100%);
}

/* Thumb */
.range-thumb {
  position: absolute;
  border-radius: 20px;
  background: var(--color-background-neutral);
  border: 2px solid var(--slider-accent);
  cursor: pointer;
  z-index: 2;
  touch-action: none; /* Prevent browser touch handling (scroll/pan) during drag */
}

.slider-container.horizontal .range-thumb {
  top: 0;
  width: 62px;
  height: 40px;
  transform: translateX(-50%);
}

.slider-container.vertical .range-thumb {
  left: 0;
  width: 40px;
  height: 62px;
  transform: translateY(50%);
}

/* Disabled state */
.slider-container.disabled {
  --slider-accent: color-mix(in srgb, var(--color-text-secondary) 50%, transparent);
}

.slider-container.disabled .range-thumb {
  cursor: not-allowed;
}

/* Muted state: visual disabled appearance but still interactive */
.slider-container.muted {
  --slider-accent: color-mix(in srgb, var(--color-text-secondary) 50%, transparent);
}

/* Inline value */
.slider-value {
  position: absolute;
  right: var(--space-04);
  color: var(--slider-accent);
  pointer-events: none;
  z-index: 3;
}

.slider-value.dragging {
  color: var(--color-brand);
}
</style>
