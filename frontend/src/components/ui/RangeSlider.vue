<!-- frontend/src/components/ui/RangeSlider.vue -->
<!-- A native <input type="range"> drawn as Milō's slider: the browser drags
     and snaps; the component maps stops to values and hands the track one
     number, where the value sits from 0 to 1, which places the fill and the
     knob — so the two glide together from stop to stop. -->
<template>
  <div :class="['slider-container', orientation, { disabled, muted, stepped: isStepped, dragging: isDragging, labeled: hasLabel }]"
    :style="{ '--fraction': fraction }">
    <span v-if="hasLabel" class="slider-label text-body">{{ label }}</span>

    <div class="slider-rail">
      <div class="range-track"></div>

      <span
        v-for="index in tickIndexes"
        :key="index"
        class="range-tick"
        :class="{ 'range-tick--passed': index <= position }"
        :style="{ '--tick': index / (stops.length - 1) }"
      ></span>

      <input
        class="range-input"
        type="range"
        :min="posMin"
        :max="posMax"
        :step="isStepped ? 1 : step"
        :value="position"
        :disabled="disabled"
        :aria-label="label || null"
        :aria-valuetext="currentLabel"
        @pointerdown="startDrag"
        @input="handleInput"
        @change="handleChange"
      />

      <span class="range-knob" aria-hidden="true"></span>
    </div>

    <!-- Beside the track, never on it: a thumb at either end covered it. -->
    <div v-if="orientation === 'horizontal' && !hideInlineValue" class="slider-value text-mono-medium">
      <template v-if="isStepped">
        <!-- Every label in one cell, all but the current one hidden: the box is as
             wide as the widest, so the track does not resize mid-drag. -->
        <span
          v-for="(stop, index) in stops"
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
import { computed, ref } from 'vue';
import { useI18n } from '@/services/i18n';
import { usePointerHold } from '@/composables/usePointerHold';
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
  // The setting's name, drawn on its own line above the track.
  label: { type: String, default: '' },
  // Discrete stops [{ value, label }], spread evenly along the track whatever
  // their values — log-spaced values give a log scale. Overrides min/max/step/
  // unit; the inline value shows the stop's label.
  steps: { type: Array, default: null },
  // A stop at every step from min to max, labeled like the continuous value:
  // for a setting chosen among a few levels rather than dialed in.
  ticks: { type: Boolean, default: false }
});

const emit = defineEmits(['update:modelValue', 'input', 'change', 'drag-start', 'drag-end']);

const { held: isDragging, press } = usePointerHold({ onRelease: stopDrag });

// Local value during drag - prevents external updates (WebSocket echo) from causing jumps
const localDragValue = ref(null);
// The value a press started from: a press that ends where it started is no commit.
let dragStartValue = null;
// The value last committed, so the browser's own change after a drag is not
// sent a second time.
let lastCommitted = null;
// Arrow keys move the input with no pointer: each step is run as a drag of its
// own, so callers that commit on drag-end (a volume, an EQ band) commit it.
let keyboardEditing = false;

// Effective value: local during drag, prop otherwise
const effectiveValue = computed(() => {
  return localDragValue.value !== null ? localDragValue.value : props.modelValue;
});

const stops = computed(() => {
  if (props.steps?.length) return props.steps;
  if (!props.ticks) return [];
  const count = Math.round((props.max - props.min) / props.step);
  return Array.from({ length: count + 1 }, (_, i) => {
    const value = parseFloat((props.min + i * props.step).toFixed(decimals.value));
    return { value, label: valueLabel(value) };
  });
});
const isStepped = computed(() => stops.value.length > 0);
const hasLabel = computed(() => Boolean(props.label) && props.orientation === 'horizontal');

// A stored value off the grid shows on the nearest stop; it is not rewritten
// until the user moves the thumb.
function stepIndex(value) {
  let best = 0;
  stops.value.forEach((stop, index) => {
    if (Math.abs(stop.value - value) < Math.abs(stops.value[best].value - value)) best = index;
  });
  return best;
}

// Where the thumb sits: the value itself, or the stop's index when stepped.
const posMin = computed(() => (isStepped.value ? 0 : props.min));
const posMax = computed(() => (isStepped.value ? stops.value.length - 1 : props.max));
const position = computed(() => (isStepped.value ? stepIndex(effectiveValue.value) : effectiveValue.value));

// Where the value sits along the track, 0 to 1 — the one number the fill reads.
// A zero/negative range (min === max, e.g. a curve point pinned between
// adjacent neighbours) sits at 0 rather than at NaN.
const fraction = computed(() => {
  const range = posMax.value - posMin.value;
  return range > 0 ? clamp((position.value - posMin.value) / range, 0, 1) : 0;
});

// Every stop, ends included.
const tickIndexes = computed(() => {
  if (!isStepped.value) return [];
  return stops.value.map((_, i) => i);
});

const { formatNumber, formatUnit } = useI18n();

const decimals = computed(() => (String(props.step).split('.')[1] ?? '').length);

function valueLabel(value, options = { maximumFractionDigits: decimals.value }) {
  return props.unit ? formatUnit(value, props.unit, options) : formatNumber(value, options);
}

const currentLabel = computed(() => (isStepped.value
  ? stops.value[position.value]?.label
  : valueLabel(effectiveValue.value)));

// Mono digits: the longest of the two bounds, at the step's precision, is the widest label.
const widestLabel = computed(() => {
  const [low, high] = [props.min, props.max].map(bound => valueLabel(bound, { minimumFractionDigits: decimals.value }));
  return high.length > low.length ? high : low;
});

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

// The input's own value, as the value it stands for: a stop's value, or the
// number at the step's precision (a float step drifts in the last digits).
function valueOf(input) {
  const raw = Number(input.value);
  if (isStepped.value) return stops.value[Math.round(raw)].value;
  return clamp(parseFloat(raw.toFixed(decimals.value)), props.min, props.max);
}

// Only the thumb takes the pointer (the input itself has pointer-events off),
// so a press is a press on the thumb: a touch on the track scrolls the page.
function startDrag(event) {
  if (props.disabled || !press(event)) return;
  localDragValue.value = props.modelValue;
  dragStartValue = props.modelValue;
  lastCommitted = null;
  emit('drag-start');
}

function handleInput(event) {
  const value = valueOf(event.target);
  if (value === effectiveValue.value) return;
  if (isDragging.value) {
    localDragValue.value = value;
  } else if (!keyboardEditing) {
    keyboardEditing = true;
    lastCommitted = null;
    emit('drag-start');
  }
  emit('update:modelValue', value);
  emit('input', value);
}

// The commit of a drag is sent here, on release, with the value dragged to:
// by the time the browser fires its own change, a caller that binds the value
// without v-model has not seen it, and the input has been put back.
function stopDrag() {
  const value = effectiveValue.value;
  if (value !== dragStartValue) {
    lastCommitted = value;
    emit('change', value);
  }
  emit('drag-end');
  localDragValue.value = null;
}

// The browser's change: after a key step, the commit; after a drag, the one
// already sent on release, skipped.
function handleChange(event) {
  const value = valueOf(event.target);
  if (value !== lastCommitted) {
    lastCommitted = value;
    emit('change', value);
  }
  if (keyboardEditing) {
    keyboardEditing = false;
    emit('drag-end');
  }
}
</script>

<style>
@property --slider-accent {
  syntax: '<color>';
  inherits: true;
  initial-value: transparent;
}

@property --fraction {
  syntax: '<number>';
  inherits: true;
  initial-value: 0;
}

/* How far the knob is held, 0 at rest to 1 held: it drives the knob's widening
   and the fill's following it, on one spring. */
@property --grow {
  syntax: '<number>';
  inherits: true;
  initial-value: 0;
}
</style>

<style scoped>
/* The Toggle's dimensions: a 34px track with its 44x28 pill knob inside it,
   3px from every side. The track and its fill are drawn by the component; the
   native input lies over them, 3px in from each end, so its thumb travels
   exactly where the knob belongs. While held, the knob widens by 4px, anchored
   where it stands — at the left end it grows to the right, at the right end to
   the left, in between in proportion — so it never eats into the track's ends,
   and the fill follows its right edge out to keep the 3px around it. */
.slider-container {
  --slider-accent: var(--color-fill-muted);
  --track-thickness: 34px;
  --knob-width: 44px;
  --knob-height: 28px;
  /* The 4px the knob widens by, as a share of its width (a length cannot
     divide a length in every engine yet). */
  --knob-grow: 0.0909;
  /* The knob plus its 3px each side: the length of track it stands on. */
  --thumb-length: calc(var(--knob-width) + 6px);
  --grow: 0;
  /* What the knob's right edge moves out by: none at the right end. */
  --fill-extra: calc(var(--grow) * 4px * (1 - var(--fraction)));
  transition: --slider-accent var(--transition-fast), --grow var(--transition-spring-light), opacity var(--transition-fast);
  display: flex;
  gap: var(--space-02);
}

.slider-container.dragging {
  --slider-accent: var(--color-brand);
  --grow: 1;
}

/* The fill and the knob both read --fraction, so they move as one. While a
   continuous slider is dragged it follows the finger untransitioned; between
   stops it glides, quickly and without a spring, so a fast sweep does not
   bounce at each stop. A value changed elsewhere (the encoder, another
   device) is not eased: the invisible thumb that takes the pointer jumps at
   once, and a knob still on its way would be pressed beside it. */

.slider-container.stepped.dragging {
  transition: --slider-accent var(--transition-fast), --fraction 120ms var(--easeOutCubic), --grow var(--transition-spring-light);
}

.slider-rail {
  position: relative;
  flex: 1;
  min-width: 0;
}

.slider-container.horizontal {
  width: 100%;
  height: 34px;
}

.slider-container.vertical {
  width: 34px;
  flex: 1;
  flex-direction: column;
}

.slider-container.vertical .slider-rail {
  min-height: 260px;
}

/* === TRACK AND FILL === */
.range-track {
  position: absolute;
  border-radius: var(--radius-full);
  background: var(--color-track);
  /* The fill's extra run past a held knob never pokes out of the track's
     rounded end, at the maximum or on the spring's overshoot. */
  overflow: hidden;
  pointer-events: none;
}

/* The fill is a pill of its own, running to the knob's far edge so the knob
   always sits on it and its rounded end wraps the knob's. */
.range-track::before {
  content: '';
  position: absolute;
  border-radius: inherit;
  background: var(--slider-accent);
}

.horizontal .range-track {
  left: 0;
  right: 0;
  top: 50%;
  height: var(--track-thickness);
  transform: translateY(-50%);
}

.horizontal .range-track::before {
  top: 0;
  bottom: 0;
  left: 0;
  width: calc(var(--thumb-length) + var(--fraction) * (100% - var(--thumb-length)) + var(--fill-extra));
}

.vertical .range-track {
  top: 0;
  bottom: 0;
  left: 50%;
  width: var(--track-thickness);
  transform: translateX(-50%);
}

.vertical .range-track::before {
  left: 0;
  right: 0;
  bottom: 0;
  height: calc(var(--thumb-length) + var(--fraction) * (100% - var(--thumb-length)) + var(--fill-extra));
}

/* === THE NATIVE INPUT === */
/* Only the thumb takes the pointer: a touch on the track lands on what is
   under it and scrolls the page, as the slider never moved on a track tap. */
.range-input {
  position: absolute;
  margin: 0;
  padding: 0;
  -webkit-appearance: none;
  appearance: none;
  background: transparent;
  pointer-events: none;
  touch-action: none;
  outline: none;
}

.horizontal .range-input {
  left: 3px;
  top: 50%;
  width: calc(100% - 6px);
  height: var(--knob-height);
  transform: translateY(-50%);
}

/* Bottom to top, as a fader is read. */
.vertical .range-input {
  top: 3px;
  left: 50%;
  width: var(--knob-height);
  height: calc(100% - 6px);
  transform: translateX(-50%);
  writing-mode: vertical-lr;
  direction: rtl;
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

/* Invisible but there: it takes the pointer, and a thumb at opacity 0 still
   does. Safari draws its own shadow under a thumb with no appearance, so the
   shadow and the border go too, not only the fill. */
.range-input::-webkit-slider-thumb {
  -webkit-appearance: none;
  appearance: none;
  width: var(--knob-width);
  height: var(--knob-height);
  border: none;
  border-radius: var(--radius-full);
  background: transparent;
  box-shadow: none;
  opacity: 0;
  cursor: pointer;
  pointer-events: auto;
}

.range-input::-moz-range-thumb {
  width: var(--knob-width);
  height: var(--knob-height);
  border: none;
  border-radius: var(--radius-full);
  background: transparent;
  box-shadow: none;
  opacity: 0;
  cursor: pointer;
  pointer-events: auto;
}

/* A fader cap across a vertical track: wider than tall, as horizontally. */
.vertical .range-input::-webkit-slider-thumb {
  width: var(--knob-height);
  height: var(--knob-width);
}

.vertical .range-input::-moz-range-thumb {
  width: var(--knob-height);
  height: var(--knob-width);
}

.range-input:disabled::-webkit-slider-thumb {
  cursor: not-allowed;
}

.range-input:disabled::-moz-range-thumb {
  cursor: not-allowed;
}

/* === KNOB === */
/* The knob seen: the native thumb under it is transparent and only takes the
   pointer, since a native thumb jumps from stop to stop and cannot glide. It
   stands where the thumb does, by the fill's own formula. Held, it widens by
   4px from where it stands along the track (its own fraction across it), so
   the end it is near stays put. */
.range-knob {
  position: absolute;
  border-radius: var(--radius-full);
  background: var(--color-thumb);
  box-shadow: var(--shadow-knob);
  pointer-events: none;
}

.horizontal .range-knob {
  top: 50%;
  left: calc(3px + var(--fraction) * (100% - var(--thumb-length)));
  width: var(--knob-width);
  height: var(--knob-height);
  transform: translateY(-50%) scaleX(calc(1 + var(--grow) * var(--knob-grow)));
  transform-origin: calc(var(--fraction) * 100%) 50%;
}

.vertical .range-knob {
  left: 50%;
  bottom: calc(3px + var(--fraction) * (100% - var(--thumb-length)));
  width: var(--knob-height);
  height: var(--knob-width);
  transform: translateX(-50%) scaleY(calc(1 + var(--grow) * var(--knob-grow)));
  transform-origin: 50% calc((1 - var(--fraction)) * 100%);
}

.range-input:focus-visible ~ .range-knob {
  box-shadow: var(--shadow-knob), 0 0 0 2px var(--color-brand);
}

/* === STOPS === */
/* The stops sit under the track, never on it: the ones the value has passed
   a light gray, the ones ahead barely there. Placed by the thumb's own formula. */
.range-tick {
  position: absolute;
  width: 3px;
  height: 3px;
  border-radius: var(--radius-full);
  background: var(--color-fill-soft);
  pointer-events: none;
  transition: background-color var(--transition-fast);
}

.range-tick--passed {
  background: var(--color-text-tertiary);
}

.slider-container.horizontal.stepped {
  margin-bottom: var(--space-03);
}

.horizontal .range-tick {
  left: calc(var(--thumb-length) / 2 + var(--tick) * (100% - var(--thumb-length)));
  top: calc(50% + var(--track-thickness) / 2 + 7px);
  transform: translate(-50%, -50%);
}

.vertical .range-tick {
  bottom: calc(var(--thumb-length) / 2 + var(--tick) * (100% - var(--thumb-length)));
  left: calc(50% + var(--track-thickness) / 2 + 7px);
  transform: translate(-50%, 50%);
}

/* === STATES === */
.slider-container.disabled {
  opacity: var(--opacity-disabled);
}

.slider-container.muted {
  --slider-accent: color-mix(in srgb, var(--color-fill-muted) 50%, transparent);
}

/* === VALUE === */
/* One width for every slider's value, so the tracks of a section line up and
   none resizes mid-drag; a label longer than that (a long translation) widens
   it, the widest label held hidden in the same cell. */
.slider-value {
  display: grid;
  place-items: center;
  flex-shrink: 0;
  min-width: 88px;
  padding: 0 var(--space-03);
  border-radius: var(--radius-full);
  background: var(--color-track);
  color: var(--slider-accent);
  white-space: nowrap;
}

.slider-value > span {
  grid-area: 1 / 1;
}

.slider-value__other {
  visibility: hidden;
}

/* With a label: the name on its own line above the track, the value in its
   pill beside the track as without one. */
.slider-container.horizontal.labeled {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  grid-template-areas:
    "label label"
    "rail value";
  gap: var(--space-03) var(--space-02);
  height: auto;
}

.slider-label {
  grid-area: label;
  min-width: 0;
  color: var(--color-text-secondary);
}

.labeled .slider-rail {
  grid-area: rail;
  height: 34px;
}

.labeled .slider-value {
  grid-area: value;
}

@media (max-aspect-ratio: 4/3) {
  .slider-container {
    --track-thickness: 30px;
    --knob-width: 38px;
    --knob-height: 24px;
    --knob-grow: 0.105;
  }

  .slider-container.horizontal {
    height: 30px;
  }

  .labeled .slider-rail {
    height: 30px;
  }

  .slider-container.vertical {
    width: 30px;
  }
}
</style>
