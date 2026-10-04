<!-- frontend/src/components/ui/RangeSlider.vue -->
<template>
  <div :class="['slider-container', orientation, { disabled, muted, dragging: isDragging }]" :style="cssVars">
    <div class="slider-rail">
      <div ref="track" class="range-track"></div>

      <span
        v-for="index in tickIndexes"
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
    </div>

    <!-- Beside the track, never on it: a thumb at either end covered it. -->
    <div v-if="orientation === 'horizontal' && !hideInlineValue" class="slider-value text-mono-medium">
      <template v-if="isStepped">
        <!-- Every label in one cell, all but the current one hidden: the box is as
             wide as the widest, so the track does not resize mid-drag. -->
        <span
          v-for="(stop, index) in steps"
          :key="stop.value"
          :class="{ 'slider-value__other': index !== position }"
        >{{ stop.label }}</span>
      </template>
      <template v-else>
        <!-- The widest reachable label, hidden, holds the box at its width for the same reason. -->
        <span class="slider-value__other">{{ widestLabel }}</span>
        <span>{{ valueLabel(effectiveValue) }}</span>
      </template>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, onMounted, onUnmounted } from 'vue';
import { useI18n } from '@/services/i18n';
import { UNITS } from '@/utils/units';

const props = defineProps({
  modelValue: { type: Number, required: true },
  min: { type: Number, default: 0 },
  max: { type: Number, default: 100 },
  step: { type: Number, default: 1 },
  orientation: { type: String, default: 'horizontal' },
  disabled: { type: Boolean, default: false },
  muted: { type: Boolean, default: false },
  // Written the way the UI language writes it (spacing, decimal comma); '' is a bare number.
  unit: { type: String, default: '', validator: (value) => value === '' || UNITS.includes(value) },
  hideInlineValue: { type: Boolean, default: false },
  // Discrete stops [{ value, label }], spread evenly along the track whatever
  // their values — log-spaced values give a log scale. Overrides min/max/step/
  // unit; the inline value shows the stop's label.
  steps: { type: Array, default: null }
});

const emit = defineEmits(['update:modelValue', 'input', 'change', 'drag-start', 'drag-end']);

const isDragging = ref(false);
const track = ref(null);
const thumbRef = ref(null);
// Layout (untransformed) sizes — for CSS positioning, which uses % of the parent's layout box.
const trackSize = ref({ width: 0, height: 0 });
const thumbAxisSize = ref(54);

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

// Every stop, ends included.
const tickIndexes = computed(() => {
  if (!isStepped.value) return [];
  return props.steps.map((_, i) => i);
});

const { formatNumber, formatUnit } = useI18n();

const decimals = computed(() => (String(props.step).split('.')[1] ?? '').length);

function valueLabel(value, options = { maximumFractionDigits: decimals.value }) {
  return props.unit ? formatUnit(value, props.unit, options) : formatNumber(value, options);
}

// Mono digits: the longest of the two bounds, at the step's precision, is the widest label.
const widestLabel = computed(() => {
  const [low, high] = [props.min, props.max].map(bound => valueLabel(bound, { minimumFractionDigits: decimals.value }));
  return high.length > low.length ? high : low;
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
  const last = props.steps.length - 1;
  return placeAt(last > 0 ? index / last : 0);
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
  --slider-accent: var(--color-fill-muted);
  transition: --slider-accent var(--transition-fast);
  display: flex;
  gap: var(--space-02);
}

/* The positioning box of the thumb and ticks: the track alone, never the value beside it */
.slider-rail {
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
  flex: 1;
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

.slider-container.horizontal .slider-rail {
  min-width: 0;
}

.slider-container.vertical {
  width: 36px;
  flex: 1;
  flex-direction: column;
}

.slider-container.vertical .slider-rail {
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
      var(--color-track) var(--progress),
      var(--color-track) 100%);
}

.slider-container.vertical .range-track {
  width: 36px;
  min-height: 260px;
  flex: 1;
  background: linear-gradient(to top,
      var(--slider-accent) 0%,
      var(--slider-accent) var(--progress),
      var(--color-track) var(--progress),
      var(--color-track) 100%);
}

/* Thumb */
.range-thumb {
  position: absolute;
  border-radius: var(--radius-full);
  background: var(--color-thumb);
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

/* Step ticks — under the thumb */
.range-tick {
  position: absolute;
  width: 4px;
  height: 4px;
  border-radius: var(--radius-full);
  background: var(--color-fill-off);
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
  --slider-accent: color-mix(in srgb, var(--color-fill-muted) 50%, transparent);
}

.slider-container.disabled .range-thumb {
  cursor: not-allowed;
}

/* Muted state: visual disabled appearance but still interactive */
.slider-container.muted {
  --slider-accent: color-mix(in srgb, var(--color-fill-muted) 50%, transparent);
}

/* Inline value */
.slider-value {
  display: grid;
  place-items: center;
  flex-shrink: 0;
  min-width: 80px;
  padding: 0 var(--space-03);
  border-radius: var(--radius-full);
  background: var(--color-inset);
  color: var(--slider-accent);
  white-space: nowrap;
}

.slider-value > span {
  grid-area: 1 / 1;
}

.slider-value__other {
  visibility: hidden;
}

.slider-container.dragging .slider-value {
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
