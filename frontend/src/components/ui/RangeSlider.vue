<!-- frontend/src/components/ui/RangeSlider.vue -->
<template>
  <div :class="['slider-container', orientation, { disabled, muted, dragging: isDragging }]" :style="cssVars">
    <div ref="track" class="range-track"></div>

    <div
      ref="thumbRef"
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
const thumbRef = ref(null);
// Layout (unscaled) sizes — for CSS positioning, which uses % of unscaled parent.
// Using BCR here would mix scaled px with unscaled % when an ancestor has transform: scale (ui_scale).
const trackSize = ref({ width: 0, height: 0 });
const thumbAxisSize = ref(54);

// Local value during drag - prevents external updates (WebSocket echo) from causing jumps
const localDragValue = ref(null);

let resizeObserver = null;
let thumbOffset = 0;
// Scaled thumb size captured fresh at drag start (drag math runs in viewport/scaled coords).
let dragThumbSize = 0;

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
  // Guard a zero/negative range (min === max, e.g. a curve point pinned between
  // adjacent neighbours) so the thumb position stays a finite number, not NaN.
  const range = props.max - props.min;
  const pct = range > 0 ? clamp((effectiveValue.value - props.min) / range, 0, 1) : 0;
  const size = thumbAxisSize.value;
  const half = size / 2;
  if (props.orientation === 'horizontal') {
    return { left: `calc(${half}px + ${pct} * (100% - ${size}px))` };
  } else {
    return { bottom: `calc(${half}px + ${pct} * (100% - ${size}px))` };
  }
});

// Progress percentage for CSS gradient (accounts for thumb size)
const percentage = computed(() => {
  const range = props.max - props.min;
  const rawPercentage = range > 0 ? ((effectiveValue.value - props.min) / range) * 100 : 0;
  const size = thumbAxisSize.value;

  if (props.orientation === 'horizontal') {
    const containerWidth = trackSize.value.width || 400;
    const thumbAdjustment = (size / containerWidth) * 100;
    return rawPercentage * (100 - thumbAdjustment) / 100 + thumbAdjustment / 2;
  } else {
    const containerHeight = trackSize.value.height || 260;
    const thumbAdjustment = (size / containerHeight) * 100;
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

  if (!track.value || !thumbRef.value) return;

  const rect = track.value.getBoundingClientRect();
  const thumbRect = thumbRef.value.getBoundingClientRect();
  const currentPct = (props.modelValue - props.min) / (props.max - props.min);
  // Use BCR (scaled) for drag math so it matches event.clientX coords.
  dragThumbSize = props.orientation === 'horizontal' ? thumbRect.width : thumbRect.height;
  const half = dragThumbSize / 2;

  if (props.orientation === 'horizontal') {
    const usableWidth = rect.width - dragThumbSize;
    const thumbCenterX = rect.left + half + (currentPct * usableWidth);
    thumbOffset = event.clientX - thumbCenterX;
  } else {
    const usableHeight = rect.height - dragThumbSize;
    const thumbCenterY = rect.bottom - half - (currentPct * usableHeight);
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
  const size = dragThumbSize;
  const half = size / 2;
  let pct;

  if (props.orientation === 'horizontal') {
    const correctedX = event.clientX - thumbOffset;
    const usableWidth = rect.width - size;
    const positionInUsableArea = correctedX - rect.left - half;
    pct = clamp(positionInUsableArea / usableWidth, 0, 1);
  } else {
    const correctedY = event.clientY - thumbOffset;
    const usableHeight = rect.height - size;
    const positionInUsableArea = rect.bottom - half - correctedY;
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

function updateSizes() {
  if (track.value) {
    trackSize.value = { width: track.value.offsetWidth, height: track.value.offsetHeight };
  }
  if (thumbRef.value) {
    thumbAxisSize.value = props.orientation === 'horizontal'
      ? thumbRef.value.offsetWidth
      : thumbRef.value.offsetHeight;
  }
}

onMounted(() => {
  updateSizes();
  resizeObserver = new ResizeObserver(updateSizes);
  if (track.value) resizeObserver.observe(track.value);
  if (thumbRef.value) resizeObserver.observe(thumbRef.value);
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

@property --progress {
  syntax: '<percentage>';
  inherits: true;
  initial-value: 0%;
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

/* Animate value changes smoothly (e.g. EQ loading), but not during drag */
.slider-container:not(.dragging) {
  transition: --slider-accent var(--transition-fast), --progress var(--transition-fast);
}

.slider-container:not(.dragging) .range-thumb {
  transition: left var(--transition-fast), bottom var(--transition-fast);
}

.slider-container.horizontal {
  width: 100%;
  height: 36px;
}

.slider-container.vertical {
  width: 36px;
  flex: 1;
  flex-direction: column;
}

/* Track */
.range-track {
  border-radius: var(--radius-full);
  pointer-events: none;
}

.slider-container.horizontal .range-track {
  width: 100%;
  height: 36px;
  background: linear-gradient(to right,
      var(--slider-accent) 0%,
      var(--slider-accent) var(--progress),
      var(--color-background-strong) var(--progress),
      var(--color-background-strong) 100%);
}

.slider-container.vertical .range-track {
  width: 36px;
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
  border-radius: var(--radius-full);
  background: var(--color-background-neutral);
  border: 2px solid var(--slider-accent);
  cursor: pointer;
  z-index: 2;
  touch-action: none; /* Prevent browser touch handling (scroll/pan) during drag */
}

.slider-container.horizontal .range-thumb {
  top: 0;
  height: 100%;
  aspect-ratio: 1.6;
  transform: translateX(-50%);
}

.slider-container.vertical .range-thumb {
  left: 0;
  width: 100%;
  aspect-ratio: 1 / 1.5;
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

/* Responsive */
@media (max-aspect-ratio: 4/3) {
  .slider-container.horizontal {
    height: 30px;
  }

  .slider-container.horizontal .range-track {
    height: 30px;
  }

  .slider-container.vertical {
    width: 30px;
  }

  .slider-container.vertical .range-track {
    width: 30px;
  }

}
</style>
