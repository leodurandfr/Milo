<!-- frontend/src/components/ui/DoubleRangeSlider.vue -->
<template>
  <div class="double-range-slider" :style="cssVars">
    <!-- Each value beside its own end of the track, never on it: a thumb at
         that end covered it. -->
    <div class="slider-value slider-value--min text-mono-medium" :class="{ dragging: isDraggingMin }">
      <!-- The widest reachable label, hidden, holds the pill at its width: a
           pill growing mid-drag would narrow the track under both thumbs. -->
      <span class="slider-value__widest">{{ widestLabel }}</span>
      <span>{{ valueLabel(modelValue.min) }}</span>
    </div>

    <div class="slider-rail">
      <div
        class="range-track"
        ref="track"
      ></div>

      <div
        ref="thumbRef"
        class="range-thumb thumb-min"
        :class="{ dragging: isDraggingMin }"
        :style="{ left: minPosition }"
        @pointerdown="startDrag($event, 'min')"
      ></div>

      <div
        class="range-thumb thumb-max"
        :class="{ dragging: isDraggingMax }"
        :style="{ left: maxPosition }"
        @pointerdown="startDrag($event, 'max')"
      ></div>
    </div>

    <div class="slider-value slider-value--max text-mono-medium" :class="{ dragging: isDraggingMax }">
      <span class="slider-value__widest">{{ widestLabel }}</span>
      <span>{{ valueLabel(modelValue.max) }}</span>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue';
import { useI18n } from '@/services/i18n';
import { UNITS } from '@/utils/units';

const props = defineProps({
  modelValue: {
    type: Object,
    required: true,
    validator: (value) => value && typeof value.min === 'number' && typeof value.max === 'number'
  },
  min: { type: Number, default: 0 },
  max: { type: Number, default: 100 },
  step: { type: Number, default: 1 },
  gap: { type: Number, default: 10 },
  // Written the way the UI language writes it (spacing, decimal comma); '' is a bare number.
  unit: { type: String, default: '', validator: (value) => value === '' || UNITS.includes(value) }
});

const emit = defineEmits(['update:modelValue', 'change']);

const isDraggingMin = ref(false);
const isDraggingMax = ref(false);
const track = ref(null);
const thumbRef = ref(null);
// Layout (untransformed) sizes — for CSS positioning, which uses % of the parent's layout box.
const trackWidth = ref(0);
const thumbAxisSize = ref(54);

let resizeObserver = null;
// Scaled thumb size captured fresh at drag start (drag math runs in viewport/scaled coords).
let dragThumbSize = 0;

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

// Pixel positions (clean)
const minPosition = computed(() => {
  const percentage = (props.modelValue.min - props.min) / (props.max - props.min);
  const size = thumbAxisSize.value;
  return `calc(${size / 2}px + ${percentage} * (100% - ${size}px))`;
});

const maxPosition = computed(() => {
  const percentage = (props.modelValue.max - props.min) / (props.max - props.min);
  const size = thumbAxisSize.value;
  return `calc(${size / 2}px + ${percentage} * (100% - ${size}px))`;
});

// Percentages for the gradient - dynamic calculation based on actual width
const minPercentageForGradient = computed(() => {
  const rawPercentage = ((props.modelValue.min - props.min) / (props.max - props.min)) * 100;
  const containerWidth = trackWidth.value || 400;
  const thumbAdjustment = (thumbAxisSize.value / containerWidth) * 100;
  return rawPercentage * (100 - thumbAdjustment) / 100 + thumbAdjustment / 2;
});

const maxPercentageForGradient = computed(() => {
  const rawPercentage = ((props.modelValue.max - props.min) / (props.max - props.min)) * 100;
  const containerWidth = trackWidth.value || 400;
  const thumbAdjustment = (thumbAxisSize.value / containerWidth) * 100;
  return rawPercentage * (100 - thumbAdjustment) / 100 + thumbAdjustment / 2;
});

// CSS variables for the gradient - uses the same logic as RangeSlider
const cssVars = computed(() => ({
  '--progress-min': `${minPercentageForGradient.value}%`,
  '--progress-max': `${maxPercentageForGradient.value}%`
}));

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function roundToStep(value) {
  return Math.round(value / props.step) * props.step;
}

function updateValues(newMin, newMax) {
  newMin = clamp(roundToStep(newMin), props.min, props.max);
  newMax = clamp(roundToStep(newMax), props.min, props.max);
  
  if (newMax - newMin < props.gap) {
    if (isDraggingMin.value) {
      newMax = Math.min(props.max, newMin + props.gap);
    } else if (isDraggingMax.value) {
      newMin = Math.max(props.min, newMax - props.gap);
    }
  }
  
  const newValue = { min: newMin, max: newMax };
  
  if (newValue.min !== props.modelValue.min || newValue.max !== props.modelValue.max) {
    emit('update:modelValue', newValue);
  }
}

// Drag handling
let dragType = null;
let thumbOffset = 0;

function startDrag(event, type) {
  if (event.button !== 0) return;

  event.preventDefault();
  event.stopPropagation();
  dragType = type;

  if (!track.value || !thumbRef.value) return;

  const rect = track.value.getBoundingClientRect();
  const thumbRect = thumbRef.value.getBoundingClientRect();
  const clickX = event.clientX;

  // Current center position of the thumb with the new calculation
  const currentPercentage = type === 'min'
    ? (props.modelValue.min - props.min) / (props.max - props.min)
    : (props.modelValue.max - props.min) / (props.max - props.min);

  // Thumb center position: half + percentage * (usable width)
  // Use BCR (scaled) for drag math so it matches event.clientX coords.
  dragThumbSize = thumbRect.width;
  const half = dragThumbSize / 2;
  const usableWidth = rect.width - dragThumbSize;
  const thumbCenterX = rect.left + half + (currentPercentage * usableWidth);

  // Offset = difference between where we click and the thumb center
  thumbOffset = clickX - thumbCenterX;
  
  if (type === 'min') {
    isDraggingMin.value = true;
  } else {
    isDraggingMax.value = true;
  }
  
  document.addEventListener('pointermove', handleDrag);
  document.addEventListener('pointerup', stopDrag);
  document.addEventListener('pointercancel', stopDrag);
}

function handleDrag(event) {
  if (!track.value || !dragType) return;

  const rect = track.value.getBoundingClientRect();
  const correctedX = event.clientX - thumbOffset;

  const size = dragThumbSize;
  const half = size / 2;
  const usableWidth = rect.width - size;
  const positionInUsableArea = correctedX - rect.left - half;
  const percentage = clamp(positionInUsableArea / usableWidth, 0, 1);
  const value = props.min + (percentage * (props.max - props.min));
  
  if (dragType === 'min') {
    updateValues(value, props.modelValue.max);
  } else {
    updateValues(props.modelValue.min, value);
  }
}

function stopDrag() {
  const wasMin = isDraggingMin.value;
  const wasMax = isDraggingMax.value;

  isDraggingMin.value = false;
  isDraggingMax.value = false;
  dragType = null;

  if (wasMin || wasMax) {
    emit('change', { min: props.modelValue.min, max: props.modelValue.max });
  }

  document.removeEventListener('pointermove', handleDrag);
  document.removeEventListener('pointerup', stopDrag);
  document.removeEventListener('pointercancel', stopDrag);
}

function updateSizes() {
  if (track.value) {
    trackWidth.value = track.value.offsetWidth;
  }
  if (thumbRef.value) {
    thumbAxisSize.value = thumbRef.value.offsetWidth;
  }
}

onMounted(() => {
  updateValues(props.modelValue.min, props.modelValue.max);
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

<style scoped>
/* Container */
.double-range-slider {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  grid-template-areas: "min rail max";
  gap: var(--space-02);
  width: 100%;
}

/* The positioning box of the thumbs: the track alone, never the values beside it */
.slider-rail {
  grid-area: rail;
  position: relative;
  display: flex;
  align-items: center;
  height: 36px;
}

/* Track with gradient identical to RangeSlider */
.range-track {
  width: 100%;
  height: 36px;
  border-radius: var(--radius-full);
  background: linear-gradient(to right,
    var(--color-track) 0%,
    var(--color-track) var(--progress-min),
    var(--color-fill-muted) var(--progress-min),
    var(--color-fill-muted) var(--progress-max),
    var(--color-track) var(--progress-max),
    var(--color-track) 100%);
  pointer-events: none;
}

/* Thumbs identical to RangeSlider */
.range-thumb {
  position: absolute;
  top: 0;
  height: 100%;
  aspect-ratio: 1.6;
  border-radius: var(--radius-full);
  background: var(--color-thumb);
  border: 2px solid var(--color-fill-muted);
  cursor: pointer;
  transform: translateX(-50%);
  touch-action: none;
}

/* Z-index for thumbs */
.thumb-min {
  z-index: 2;
}

.thumb-max {
  z-index: 3;
}

.thumb-max.dragging {
  z-index: 4;
}

/* Values — the same pill as RangeSlider's */
.slider-value {
  display: grid;
  place-items: center;
  min-width: 80px;
  padding: 0 var(--space-03);
  border-radius: var(--radius-full);
  background: var(--color-inset);
  color: var(--color-text-secondary);
  white-space: nowrap;
  transition: color var(--transition-fast);
}

.slider-value > span {
  grid-area: 1 / 1;
}

.slider-value__widest {
  visibility: hidden;
}

.slider-value--min {
  grid-area: min;
}

.slider-value--max {
  grid-area: max;
}

.slider-value.dragging {
  color: var(--color-brand);
}

/* Responsive */
@media (max-aspect-ratio: 4/3) {
  /* Two pills beside a phone-width track leave the thumbs no travel: they go
     under it, each below its own end. */
  .double-range-slider {
    grid-template-columns: auto auto;
    grid-template-areas:
      "rail rail"
      "min max";
  }

  .slider-rail {
    height: 30px;
  }

  .slider-value {
    height: 30px;
  }

  .slider-value--min {
    justify-self: start;
  }

  .slider-value--max {
    justify-self: end;
  }

  .range-track {
    height: 30px;
    border-radius: var(--radius-04);
  }

}
</style>