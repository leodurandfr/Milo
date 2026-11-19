<!-- frontend/src/components/ui/LevelSlider.vue -->
<!-- A level drawn as one thick bar filled from the left, with no knob: the
     finger moves the level by how far it travels, never to where it lands, so
     a touch that lands on the bar changes nothing until it moves. Held, the
     bar swells; pulled past either end, it stretches that way and springs
     back on release. A speaker at its start shows as many waves as the level
     reaches (none at the minimum, a cross when muted), the value at its end. -->
<template>
  <div
    :class="['level-slider', { dragging: isDragging, held: isHeld, muted, disabled }]"
    :style="{ '--ls-fraction': fraction, '--ls-pull-start': `${pullStart}px`, '--ls-pull-end': `${pullEnd}px` }"
    role="slider"
    :tabindex="disabled ? -1 : 0"
    :aria-label="label || null"
    :aria-valuemin="min"
    :aria-valuemax="max"
    :aria-valuenow="effectiveValue"
    :aria-valuetext="valueText"
    :aria-disabled="disabled || null"
    @pointerdown="startDrag"
    @pointermove="moveDrag"
    @keydown="handleKey"
    @keyup="handleKeyUp"
    @blur="endKey"
  >
    <div ref="trackRef" class="level-slider__track">
      <!-- Drawn twice, once on the track and once on the fill, which is
           clipped to the level: the icon and the figure change ink exactly
           where the fill passes under them. -->
      <div
        v-for="layer in LAYERS"
        :key="layer"
        :class="['level-slider__layer', `level-slider__layer--${layer}`]"
      >
        <svg class="level-slider__icon" viewBox="0 0 24 24" aria-hidden="true">
          <path fill="currentColor" :d="SPEAKER_PATH" />
          <path
            v-for="(wave, index) in WAVE_PATHS"
            :key="wave"
            class="level-slider__mark"
            :class="{ shown: !muted && index < waveCount }"
            :d="wave"
          />
          <path class="level-slider__mark" :class="{ shown: muted }" :d="MUTE_PATH" />
        </svg>
        <span class="level-slider__value text-mono-small">{{ valueText }}</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue';
import { useI18n } from '@/services/i18n';
import { usePointerHold } from '@/composables/usePointerHold';
import { useTimer } from '@/composables/useTimer';
import { UNITS } from '@/utils/units';

const props = defineProps({
  modelValue: { type: Number, required: true },
  min: { type: Number, default: 0 },
  max: { type: Number, default: 100 },
  step: { type: Number, default: 1 },
  disabled: { type: Boolean, default: false },
  muted: { type: Boolean, default: false },
  // Written the way the UI language writes it (spacing, decimal comma); '' is a bare number.
  unit: { type: String, default: '', validator: (value) => value === '' || UNITS.includes(value) },
  // The accessible name: the bar draws no label of its own.
  label: { type: String, default: '' }
});

const emit = defineEmits(['update:modelValue', 'input', 'change', 'drag-start', 'drag-end']);

// The speaker of icons/volume-off.svg, and the marks drawn beside it: three
// waves, as many shown as the level reaches, or the cross when muted.
const SPEAKER_PATH = 'M14.604 3.839c.244.13.396.385.396.661l0 15c0 .277-.152.531-.396.661-.244.131-.54.116-.77-.037l-6.561-4.374-4.273 0c-.414 0-.75-.336-.75-.75l0-6c0-.414.336-.75.75-.75l4.273 0 6.561-4.374c.23-.153.526-.168.77-.037zM13.5 5.9l-5.25 3.501 0 5.198 5.25 3.5zM6.75 9.75l-3 0 0 4.5 3 0z';
const WAVE_PATHS = ['M16.51 9.77A3 3 0 0 1 16.51 14.23', 'M18.35 7.73A5.75 5.75 0 0 1 18.35 16.27', 'M20.19 5.68A8.5 8.5 0 0 1 20.19 18.32'];
const MUTE_PATH = 'M17.25 9.25l5.5 5.5M22.75 9.25l-5.5 5.5';
const LAYERS = ['track', 'fill'];

const { formatNumber, formatUnit } = useI18n();
const timer = useTimer();

// Touch waits a beat before swelling: a finger that is starting to scroll the
// list is cancelled by the browser within that beat, and no bar pulses under it.
const TOUCH_SWELL_DELAY_MS = 120;
// How far a pull past an end stretches the bar, as iOS damps it: the share of
// the overshoot kept falls as the overshoot grows, towards PULL_MAX_PX however
// far the finger goes.
const PULL_DAMPING = 0.55;
const PULL_MAX_PX = 16;
// A touch moves the level only once it has travelled this far, and further
// sideways than up or down: before that it may be a scroll of the list the
// browser has not claimed yet, and a level set by it would reach the speaker.
const TOUCH_SLOP_PX = 8;

const { held: isDragging, press } = usePointerHold({ onRelease: stopDrag });
const isHeld = ref(false);
const trackRef = ref(null);
const localDragValue = ref(null);
const pullStart = ref(0);
const pullEnd = ref(0);

let drag = null;
let swellTimer = null;
let keyboardEditing = false;
let keyStartValue = null;

const effectiveValue = computed(() => (localDragValue.value !== null ? localDragValue.value : props.modelValue));

const fraction = computed(() => {
  const range = props.max - props.min;
  return range > 0 ? clamp((effectiveValue.value - props.min) / range, 0, 1) : 0;
});

// None at the minimum, then one wave per third of the travel.
const waveCount = computed(() => (fraction.value <= 0 ? 0 : Math.min(3, Math.ceil(fraction.value * 3))));

const decimals = computed(() => (String(props.step).split('.')[1] ?? '').length);

const valueText = computed(() => {
  const options = { maximumFractionDigits: decimals.value };
  return props.unit ? formatUnit(effectiveValue.value, props.unit, options) : formatNumber(effectiveValue.value, options);
});

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function snap(value) {
  const stepped = props.min + Math.round((value - props.min) / props.step) * props.step;
  return clamp(parseFloat(stepped.toFixed(decimals.value)), props.min, props.max);
}

function rubber(overshoot, dimension) {
  return (1 - 1 / ((overshoot * PULL_DAMPING) / dimension + 1)) * dimension;
}

function setValue(value) {
  if (value === effectiveValue.value) return;
  if (isDragging.value) localDragValue.value = value;
  emit('update:modelValue', value);
  emit('input', value);
}

function startDrag(event) {
  if (props.disabled || !press(event)) return;
  const width = trackRef.value?.getBoundingClientRect().width || 1;
  const touch = event.pointerType === 'touch';
  drag = { x0: event.clientX, y0: event.clientY, start: props.modelValue, width, engaged: false, abandoned: false };
  event.currentTarget.setPointerCapture?.(event.pointerId);
  if (touch) {
    swellTimer = timer.setTimeout(() => { isHeld.value = isDragging.value && !drag?.abandoned; }, TOUCH_SWELL_DELAY_MS);
  } else {
    engage(event);
  }
}

// The press becomes an edit: the level follows from here, with no jump.
function engage(event) {
  drag.engaged = true;
  drag.x0 = event.clientX;
  // A level that moved during the touch's slop is the one the edit starts from.
  drag.start = props.modelValue;
  localDragValue.value = props.modelValue;
  isHeld.value = true;
  emit('drag-start');
}

function moveDrag(event) {
  if (!isDragging.value || !drag || drag.abandoned || props.disabled) return;
  if (!drag.engaged) {
    const dx = Math.abs(event.clientX - drag.x0);
    const dy = Math.abs(event.clientY - drag.y0);
    if (Math.max(dx, dy) < TOUCH_SLOP_PX) return;
    if (dy >= dx) {
      drag.abandoned = true;
      isHeld.value = false;
      return;
    }
    engage(event);
  }
  const dx = event.clientX - drag.x0;
  const range = props.max - props.min;
  const raw = drag.start + (dx / drag.width) * range;
  const overshoot = raw > props.max
    ? ((raw - props.max) / range) * drag.width
    : raw < props.min ? ((props.min - raw) / range) * drag.width : 0;
  const pull = rubber(overshoot, PULL_MAX_PX);
  pullEnd.value = raw > props.max ? pull : 0;
  pullStart.value = raw < props.min ? pull : 0;
  setValue(snap(raw));
}

function stopDrag() {
  if (swellTimer !== null) timer.clear(swellTimer);
  swellTimer = null;
  if (drag?.engaged) {
    const value = effectiveValue.value;
    if (value !== drag.start) emit('change', value);
    emit('drag-end');
  }
  drag = null;
  isHeld.value = false;
  pullStart.value = 0;
  pullEnd.value = 0;
  localDragValue.value = null;
}

// The keys a native range input takes: arrows step, Page keys move a tenth of
// the range, Home and End go to the ends. A run of presses is one edit,
// committed when the key goes up, so callers that commit on drag-end (a
// volume) commit it once.
const KEY_TARGETS = {
  ArrowRight: (value) => value + props.step,
  ArrowUp: (value) => value + props.step,
  ArrowLeft: (value) => value - props.step,
  ArrowDown: (value) => value - props.step,
  PageUp: (value) => value + (props.max - props.min) / 10,
  PageDown: (value) => value - (props.max - props.min) / 10,
  Home: () => props.min,
  End: () => props.max
};

function handleKey(event) {
  const target = KEY_TARGETS[event.key];
  if (!target || props.disabled) return;
  event.preventDefault();
  const value = snap(target(props.modelValue));
  // A key at the end it points to (Home at the minimum) starts no edit.
  if (value === props.modelValue) return;
  if (!keyboardEditing) {
    keyboardEditing = true;
    keyStartValue = props.modelValue;
    emit('drag-start');
  }
  setValue(value);
}

function handleKeyUp(event) {
  if (KEY_TARGETS[event.key]) endKey();
}

function endKey() {
  if (!keyboardEditing) return;
  keyboardEditing = false;
  if (props.modelValue !== keyStartValue) emit('change', props.modelValue);
  emit('drag-end');
}
</script>

<style>
@property --ls-grow {
  syntax: '<number>';
  inherits: true;
  initial-value: 0;
}

@property --ls-pull-start {
  syntax: '<length>';
  inherits: true;
  initial-value: 0px;
}

@property --ls-pull-end {
  syntax: '<length>';
  inherits: true;
  initial-value: 0px;
}
</style>

<style scoped>
/* At rest the bar is the rail RangeSlider stands on (--rail-height, a pill),
   filled with the secondary text tone, as a Toggle beside it is when on.
   Held, it swells by --ls-swell in height and half of that on each side —
   height and width, never a scale, so what is drawn inside is not stretched;
   on a plain 200ms ease, since a spring over 4px reads only as a delay — its
   track darkens a step, its speaker comes up to full contrast and its value
   takes the brand. A pull past an end lengthens it on that side only and
   thins it a little; the release springs both back. */
.level-slider {
  --ls-fill: var(--color-text-secondary);
  /* The speaker's ink and the value's, on the track and on the fill. */
  --ls-icon: var(--color-text);
  --ls-icon-on-fill: var(--color-text-on-secondary);
  --ls-value: var(--color-text-secondary);
  --ls-value-on-fill: var(--color-text-on-secondary);
  --ls-track: var(--color-fill-faint);
  --ls-swell: 4px;
  --ls-grow: 0;
  position: relative;
  width: 100%;
  height: var(--rail-height);
  touch-action: pan-y;
  cursor: grab;
  outline: none;
  user-select: none;
  -webkit-tap-highlight-color: transparent;
  transition: --ls-grow var(--transition-fast), --ls-pull-start var(--transition-spring-light), --ls-pull-end var(--transition-spring-light), opacity var(--transition-fast);
}

/* The pull follows the finger untransitioned while held. */
.level-slider.dragging {
  cursor: grabbing;
  transition: --ls-grow var(--transition-fast), opacity var(--transition-fast);
}

.level-slider.held {
  --ls-grow: 1;
  --ls-track: var(--color-fill-faint-held);
  --ls-value: var(--color-brand);
  --ls-value-on-fill: var(--color-brand);
}

/* Muted is not disabled: the level still moves, nothing sounds — and the
   value never takes the brand while dragged, since what is set is not heard. */
.level-slider.muted {
  --ls-fill: var(--color-fill-soft);
  --ls-icon-on-fill: var(--color-text-secondary);
  --ls-value-on-fill: var(--color-text-secondary);
}

.level-slider.muted.held {
  --ls-icon-on-fill: var(--color-text);
  --ls-value: var(--color-text-secondary);
  --ls-value-on-fill: var(--color-text-secondary);
}

.level-slider.disabled {
  opacity: var(--opacity-disabled);
  cursor: not-allowed;
}

.level-slider__track {
  position: absolute;
  top: 50%;
  left: calc(var(--ls-grow) * var(--ls-swell) / -2 - var(--ls-pull-start));
  right: calc(var(--ls-grow) * var(--ls-swell) / -2 - var(--ls-pull-end));
  height: calc(var(--rail-height) + var(--ls-grow) * var(--ls-swell) - (var(--ls-pull-start) + var(--ls-pull-end)) * 0.25);
  transform: translateY(-50%);
  border-radius: var(--radius-full);
  background: var(--ls-track);
  overflow: hidden;
  transition: background-color var(--transition-fast);
}

.level-slider:focus-visible .level-slider__track {
  box-shadow: 0 0 0 2px var(--color-brand);
}

/* Both layers span the whole track; the fill's is clipped to the level, so
   its copies sit exactly over the ones under them. */
.level-slider__layer {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 var(--space-04) 0 var(--space-03);
  pointer-events: none;
}

.level-slider__layer--fill {
  background: var(--ls-fill);
  clip-path: inset(0 calc((1 - var(--ls-fraction)) * 100%) 0 0 round var(--radius-full));
  transition: background-color var(--transition-fast);
}

.level-slider__icon {
  flex-shrink: 0;
  color: var(--ls-icon);
  /* A shade under full contrast at rest, full while held. */
  opacity: 0.7;
  transition: color var(--transition-fast), opacity var(--transition-fast);
  width: 20px;
  height: 20px;
}

.level-slider__mark {
  fill: none;
  stroke: currentColor;
  stroke-width: 1.5;
  stroke-linecap: round;
  opacity: 0;
  transition: opacity var(--transition-fast);
}

.level-slider.held .level-slider__icon {
  opacity: 1;
}

.level-slider__layer--fill .level-slider__icon {
  color: var(--ls-icon-on-fill);
}

.level-slider__layer--fill .level-slider__value {
  color: var(--ls-value-on-fill);
}

.level-slider__mark.shown {
  opacity: 1;
}

.level-slider__value {
  color: var(--ls-value);
  transition: color var(--transition-fast);
  white-space: nowrap;
}

@media (prefers-reduced-motion: reduce) {
  .level-slider,
  .level-slider.dragging {
    transition: opacity var(--transition-fast);
  }
}
</style>
