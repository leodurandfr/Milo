<!-- frontend/src/components/ui/RangeSlider.vue -->
<template>
  <div :class="['slider-container', orientation, { disabled, muted, dragging: isDragging }]" :style="cssVars">
    <div ref="track" class="range-track"></div>

    <span
      v-for="index in visibleTickIndexes"
      :key="index"
      class="range-tick"
      :style="tickStyle(index)"
    ></span>

    <div
      ref="thumbRef"
      class="range-thumb"
      :class="{ dragging: isDragging }"
      :style="thumbStyle"
      @pointerdown="startDrag"
    ></div>

    <div v-if="orientation === 'horizontal' && !hideInlineValue" ref="valueRef" class="slider-value text-mono-medium" :class="{ dragging: isDragging, muted: muted, stepped: isStepped }">
      <template v-if="isStepped">
        <!-- Every label in one cell, all but the current one hidden: the box is as
             wide as the widest, so the ticks it hides do not change mid-drag. -->
        <span
          v-for="(stop, index) in steps"
          :key="stop.value"
          :class="{ 'slider-value__other': index !== position }"
        >{{ stop.label }}</span>
      </template>
      <template v-else>{{ effectiveValue }}{{ valueUnit }}</template>
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
  hideInlineValue: { type: Boolean, default: false },
  // Discrete stops [{ value, label }], spread evenly along the track whatever
  // their values — log-spaced values give a log scale. Overrides min/max/step/
  // valueUnit; the inline value shows the stop's label.
  steps: { type: Array, default: null }
});

const emit = defineEmits(['update:modelValue', 'input', 'change', 'drag-start', 'drag-end']);

const isDragging = ref(false);
const track = ref(null);
const thumbRef = ref(null);
// Layout (unscaled) sizes — for CSS positioning, which uses % of unscaled parent.
// Using BCR here would mix scaled px with unscaled % when an ancestor has transform: scale (ui_scale).
const trackSize = ref({ width: 0, height: 0 });
const thumbAxisSize = ref(54);
const valueRef = ref(null);
// Left edge of the inline value (right-anchored, so it moves with the text's width).
const valueLeft = ref(Infinity);
const TICK_CLEARANCE = 8;

// Local value during drag - prevents external updates (WebSocket echo) from causing jumps
const localDragValue = ref(null);

let resizeObserver = null;
let thumbOffset = 0;
// Scaled thumb size captured fresh at drag start (drag math runs in viewport/scaled coords).
let dragThumbSize = 0;
// A press that ends where it started is not a change: no commit for a tap.
let dragStartValue = null;

// Effective value: local during drag, prop otherwise
const effectiveValue = computed(() => {
  return localDragValue.value !== null ? localDragValue.value : props.modelValue;
});

const isStepped = computed(() => (props.steps?.length ?? 0) > 0);

// A stored value off the grid shows on the nearest stop; it is not rewritten
// until the user moves the thumb.
function stepIndex(value) {
  let best = 0;
  props.steps.forEach((stop, index) => {
    if (Math.abs(stop.value - value) < Math.abs(props.steps[best].value - value)) best = index;
  });
  return best;
}

// Where the thumb sits: the value itself, or the stop's index when stepped.
const posMin = computed(() => (isStepped.value ? 0 : props.min));
const posMax = computed(() => (isStepped.value ? props.steps.length - 1 : props.max));
const position = computed(() => (isStepped.value ? stepIndex(effectiveValue.value) : effectiveValue.value));

// Interior stops only — the track's ends already mark the first and last — and
// none under the inline value, which on a phone-width track covers a quarter
// of it. Same unscaled layout units as trackSize.
const visibleTickIndexes = computed(() => {
  if (!isStepped.value) return [];
  const last = props.steps.length - 1;
  const size = thumbAxisSize.value;
  const usable = trackSize.value.width - size;
  const indexes = Array.from({ length: last - 1 }, (_, i) => i + 1);
  if (props.orientation !== 'horizontal') return indexes;
  return indexes.filter(i => size / 2 + (i / last) * usable < valueLeft.value - TICK_CLEARANCE);
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
  const range = posMax.value - posMin.value;
  const pct = range > 0 ? clamp((position.value - posMin.value) / range, 0, 1) : 0;
  return placeAt(pct);
});

function placeAt(pct) {
  const size = thumbAxisSize.value;
  const half = size / 2;
  if (props.orientation === 'horizontal') {
    return { left: `calc(${half}px + ${pct} * (100% - ${size}px))` };
  } else {
    return { bottom: `calc(${half}px + ${pct} * (100% - ${size}px))` };
  }
}

function tickStyle(index) {
  return placeAt(index / (props.steps.length - 1));
}

// Progress percentage for CSS gradient (accounts for thumb size)
const percentage = computed(() => {
  const range = posMax.value - posMin.value;
  const rawPercentage = range > 0 ? ((position.value - posMin.value) / range) * 100 : 0;
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
  const currentPosition = isStepped.value ? stepIndex(props.modelValue) : props.modelValue;
  const currentRange = posMax.value - posMin.value;
  const currentPct = currentRange > 0 ? (currentPosition - posMin.value) / currentRange : 0;
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
  dragStartValue = props.modelValue;
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

  const value = isStepped.value
    ? props.steps[Math.round(pct * (props.steps.length - 1))].value
    : clamp(roundToStep(props.min + pct * (props.max - props.min)), props.min, props.max);
  if (value === localDragValue.value) return;

  localDragValue.value = value;
  emit('update:modelValue', value);
  emit('input', value);
}

function stopDrag() {
  if (isDragging.value) {
    isDragging.value = false;
    if (effectiveValue.value !== dragStartValue) emit('change', effectiveValue.value);
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
  if (valueRef.value) {
    valueLeft.value = valueRef.value.offsetLeft;
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
  if (valueRef.value) resizeObserver.observe(valueRef.value);
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

/* Sized off the track, not the container: `flex: 1` above only means "fill the
   height" in a column parent — in a row one it stretches the *width* instead,
   and a container-relative thumb followed it to the full width of the host. */
.slider-container.vertical .range-thumb {
  left: 50%;
  width: 36px;
  aspect-ratio: 1 / 1.5;
  transform: translate(-50%, 50%);
}

/* Step ticks — under the thumb and the inline value */
.range-tick {
  position: absolute;
  width: 4px;
  height: 4px;
  border-radius: var(--radius-full);
  background: var(--color-text-light);
  pointer-events: none;
  z-index: 1;
}

.slider-container.horizontal .range-tick {
  top: 50%;
  transform: translate(-50%, -50%);
}

.slider-container.vertical .range-tick {
  left: 50%;
  transform: translate(-50%, 50%);
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

.slider-value.stepped {
  display: grid;
  justify-items: end;
}

.slider-value.stepped > span {
  grid-area: 1 / 1;
}

.slider-value__other {
  visibility: hidden;
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

  .slider-container.vertical .range-thumb {
    width: 30px;
  }

}
</style>
