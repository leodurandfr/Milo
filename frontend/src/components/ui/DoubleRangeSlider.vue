<!-- frontend/src/components/ui/DoubleRangeSlider.vue -->
<!-- Two native <input type="range"> over one track, drawn as RangeSlider is:
     the browser drags and places each thumb, the component keeps the gap
     between them and hands the fill where each value sits, 0 to 1. -->
<template>
  <div class="double-range-slider"
    :class="{ dragging: draggingThumb !== null, 'dragging-min': draggingThumb === 'min', 'dragging-max': draggingThumb === 'max', labeled: Boolean(label) }"
    :style="{ '--fraction-min': fractionOf(modelValue.min), '--fraction-max': fractionOf(modelValue.max) }">
    <span v-if="label" class="slider-label text-body">{{ label }}</span>

    <!-- Each value beside its own end of the track, never on it: a thumb at
         that end covered it. -->
    <div class="slider-value slider-value--min text-mono-medium" :class="{ dragging: draggingThumb === 'min' }">
      <!-- The widest reachable label, hidden, holds the pill at its width: a
           pill growing mid-drag would narrow the track under both thumbs. -->
      <span class="slider-value__widest">{{ widestLabel }}</span>
      <span>{{ valueLabel(modelValue.min) }}</span>
    </div>

    <div class="slider-rail">
      <div class="range-track"></div>

      <input
        v-for="thumb in THUMBS"
        :key="thumb"
        class="range-input"
        :class="`range-input--${thumb}`"
        type="range"
        :min="min"
        :max="max"
        :step="step"
        :value="modelValue[thumb]"
        :aria-label="label || null"
        :aria-valuetext="valueLabel(modelValue[thumb])"
        @pointerdown="startDrag($event, thumb)"
        @input="handleInput($event, thumb)"
        @change="emit('change', { ...lastValue })"
      />
    </div>

    <div class="slider-value slider-value--max text-mono-medium" :class="{ dragging: draggingThumb === 'max' }">
      <span class="slider-value__widest">{{ widestLabel }}</span>
      <span>{{ valueLabel(modelValue.max) }}</span>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue';
import { useI18n } from '@/services/i18n';
import { usePointerHold } from '@/composables/usePointerHold';
import { UNITS } from '@/utils/units';

const THUMBS = ['min', 'max'];

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
  unit: { type: String, default: '', validator: (value) => value === '' || UNITS.includes(value) },
  // The setting's name, drawn on its own line above the track.
  label: { type: String, default: '' }
});

const emit = defineEmits(['update:modelValue', 'change']);

// The thumb held now, 'min' or 'max', else null.
const draggingThumb = ref(null);
const { press } = usePointerHold({ onRelease: () => { draggingThumb.value = null; } });
// The pair last emitted: the browser's change can land before the parent's
// new value has come back down as the prop.
let lastValue = { ...props.modelValue };

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

// Where a value sits along the track, 0 to 1: the one number the fill reads.
function fractionOf(value) {
  const range = props.max - props.min;
  return range > 0 ? clamp((value - props.min) / range, 0, 1) : 0;
}

function roundToStep(value) {
  return parseFloat((Math.round(value / props.step) * props.step).toFixed(decimals.value));
}

// The thumb being moved pushes the other one along rather than crossing the
// gap; at the end of the track, it stops instead.
function updateValues(newMin, newMax, moved) {
  newMin = clamp(roundToStep(newMin), props.min, props.max);
  newMax = clamp(roundToStep(newMax), props.min, props.max);

  if (newMax - newMin < props.gap) {
    if (moved === 'min') {
      newMax = Math.min(props.max, newMin + props.gap);
      newMin = Math.min(newMin, newMax - props.gap);
    } else {
      newMin = Math.max(props.min, newMax - props.gap);
      newMax = Math.max(newMax, newMin + props.gap);
    }
  }

  lastValue = { min: newMin, max: newMax };
  if (newMin !== props.modelValue.min || newMax !== props.modelValue.max) {
    emit('update:modelValue', { ...lastValue });
  }
  return lastValue;
}

function handleInput(event, thumb) {
  const raw = Number(event.target.value);
  const next = thumb === 'min'
    ? updateValues(raw, props.modelValue.max, 'min')
    : updateValues(props.modelValue.min, raw, 'max');
  // A thumb held back by the gap stays where the gap holds it, not under the
  // finger — the input is told, since its value did not change for Vue.
  if (next[thumb] !== raw) event.target.value = next[thumb];
}

// Only the thumbs take the pointer (each input has pointer-events off), so a
// press is a press on a thumb, and a touch on the track scrolls the page.
function startDrag(event, thumb) {
  if (press(event)) draggingThumb.value = thumb;
}

onMounted(() => updateValues(props.modelValue.min, props.modelValue.max, 'min'));
</script>

<style>
/* Registered here too (RangeSlider registers the same), so the transitions
   below run whichever of the two loaded first. */
@property --slider-accent {
  syntax: '<color>';
  inherits: true;
  initial-value: transparent;
}

/* How far each knob is held, 0 at rest to 1 held (RangeSlider's --grow, one
   per knob). */
@property --grow-min {
  syntax: '<number>';
  inherits: true;
  initial-value: 0;
}

@property --grow-max {
  syntax: '<number>';
  inherits: true;
  initial-value: 0;
}
</style>

<style scoped>
/* RangeSlider's track and knobs (the Toggle's dimensions), with a value beside
   each end and, with a label, the name on its own line above. The two native
   inputs lie over the track 3px in from each end, so their thumbs travel
   where the knobs belong. */
.double-range-slider {
  --slider-accent: var(--color-fill-muted);
  --track-thickness: 34px;
  --knob-width: 44px;
  --knob-height: 28px;
  --knob-grow: 0.0909;
  --thumb-length: calc(var(--knob-width) + 6px);
  --grow-min: 0;
  --grow-max: 0;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  grid-template-areas: "min rail max";
  gap: var(--space-02);
  width: 100%;
  transition:
    --slider-accent var(--transition-fast),
    --grow-min var(--transition-spring-light),
    --grow-max var(--transition-spring-light);
}

.double-range-slider.dragging {
  --slider-accent: var(--color-brand);
}

.double-range-slider.dragging-min {
  --grow-min: 1;
}

.double-range-slider.dragging-max {
  --grow-max: 1;
}

.double-range-slider.labeled {
  grid-template-areas:
    "label label label"
    "min rail max";
  row-gap: var(--space-03);
}

.slider-label {
  grid-area: label;
  min-width: 0;
  color: var(--color-text-secondary);
}

/* The positioning box of the thumbs: the track alone, never the values beside it */
.slider-rail {
  grid-area: rail;
  position: relative;
  height: 34px;
}

.range-track {
  position: absolute;
  left: 0;
  right: 0;
  top: 50%;
  height: var(--track-thickness);
  transform: translateY(-50%);
  border-radius: var(--radius-full);
  background: var(--color-track);
  /* The fill's extra run past a held knob never pokes out of the track's
     rounded end, at the maximum or on the spring's overshoot. */
  overflow: hidden;
  pointer-events: none;
}

/* The fill is a pill of its own, from the low knob's outer edge to the high
   knob's, so its rounded ends wrap both knobs. */
.range-track::before {
  content: '';
  position: absolute;
  top: 0;
  bottom: 0;
  /* Each end follows its knob's outer edge out as it widens: the low knob's
     left edge, none at the left end; the high knob's right, none at the right. */
  --extra-min: calc(var(--grow-min) * 4px * var(--fraction-min));
  --extra-max: calc(var(--grow-max) * 4px * (1 - var(--fraction-max)));
  left: calc(var(--fraction-min) * (100% - var(--thumb-length)) - var(--extra-min));
  width: calc(var(--thumb-length) + (var(--fraction-max) - var(--fraction-min)) * (100% - var(--thumb-length)) + var(--extra-min) + var(--extra-max));
  border-radius: inherit;
  background: var(--slider-accent);
}

/* Only the thumbs take the pointer: a touch on the track scrolls the page. */
.range-input {
  position: absolute;
  left: 3px;
  top: 50%;
  width: calc(100% - 6px);
  height: var(--knob-height);
  margin: 0;
  padding: 0;
  transform: translateY(-50%);
  -webkit-appearance: none;
  appearance: none;
  background: transparent;
  pointer-events: none;
  touch-action: none;
  outline: none;
}

/* The high thumb above the low one, and the held one above both. */
.range-input--min {
  z-index: 2;
}

.range-input--max {
  z-index: 3;
}

.dragging-min .range-input--min {
  z-index: 4;
}

.range-input::-webkit-slider-runnable-track {
  height: 100%;
  background: transparent;
  border: none;
}

.range-input::-moz-range-track {
  height: 100%;
  background: transparent;
  border: none;
}

.range-input::-webkit-slider-thumb {
  -webkit-appearance: none;
  appearance: none;
  width: var(--knob-width);
  height: var(--knob-height);
  border: none;
  border-radius: var(--radius-full);
  background: var(--color-thumb);
  box-shadow: var(--shadow-knob);
  cursor: pointer;
  pointer-events: auto;
}

.range-input::-moz-range-thumb {
  width: var(--knob-width);
  height: var(--knob-height);
  border: none;
  border-radius: var(--radius-full);
  background: var(--color-thumb);
  box-shadow: var(--shadow-knob);
  cursor: pointer;
  pointer-events: auto;
}

/* Widened by 4px from where each knob stands along the track, so the end it
   is near stays put. */
.range-input--min {
  --grow: var(--grow-min);
  --at: var(--fraction-min);
}

.range-input--max {
  --grow: var(--grow-max);
  --at: var(--fraction-max);
}

.range-input::-webkit-slider-thumb {
  transform: scaleX(calc(1 + var(--grow) * var(--knob-grow)));
  transform-origin: calc(var(--at) * 100%) 50%;
}

.range-input::-moz-range-thumb {
  transform: scaleX(calc(1 + var(--grow) * var(--knob-grow)));
  transform-origin: calc(var(--at) * 100%) 50%;
}

.range-input:focus-visible::-webkit-slider-thumb {
  box-shadow: var(--shadow-knob), 0 0 0 2px var(--color-brand);
}

.range-input:focus-visible::-moz-range-thumb {
  box-shadow: var(--shadow-knob), 0 0 0 2px var(--color-brand);
}

/* Values — the same pill as RangeSlider's */
.slider-value {
  display: grid;
  place-items: center;
  min-width: 88px;
  padding: 0 var(--space-03);
  border-radius: var(--radius-full);
  background: var(--color-track);
  color: var(--color-fill-muted);
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
  .double-range-slider {
    --track-thickness: 30px;
    --knob-width: 38px;
    --knob-height: 24px;
    --knob-grow: 0.105;
    grid-template-columns: auto auto;
    grid-template-areas:
      "rail rail"
      "min max";
  }

  /* Two pills beside a phone-width track leave the thumbs no travel: they go
     under it, each below its own end. */
  .double-range-slider.labeled {
    grid-template-areas:
      "label label"
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
}
</style>
