<!-- frontend/src/components/equalizer/EQBand.vue -->
<!-- Individual EQ band control: band label and gain slider -->
<!-- The class names .gain-slider and .gain-value are read as anchors by
     tests/architecture/equalizerTargetScope.test.js, which pins that neither
     renders a figure before the store has loaded one: the zeroed placeholder
     never shows, and the slider draws only a gain a record carried — the
     previous target's, held for at most HOLD_MS. Renaming either is fine;
     update PRINTED_GAIN_CLASS / GAIN_SLIDER_CLASS there in the same commit. -->
<template>
  <div class="eq-band" :class="{ 'horizontal': orientation === 'horizontal', 'compact': compact }">
    <div class="band-label text-mono-small">{{ displayName }}</div>

    <div class="gain-slider" :class="{ 'pending': !loaded && !holding }">
      <RangeSlider
        :model-value="shownGain"
        :min="-15"
        :max="15"
        :step="0.5"
        :orientation="sliderOrientation"
        :disabled="disabled"
        :hide-inline-value="true"
        :glide="placed"
        @input="handleGainInput"
        @change="handleGainChange"
        @drag-start="isDragging = true"
        @drag-end="handleDragEnd"
      />
    </div>

    <div class="gain-value text-mono-small" :class="{ 'dragging': isDragging }">
      {{ loaded ? formatNumber(gainValue, GAIN_FORMAT) : '—' }}
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue';
import RangeSlider from '@/components/ui/RangeSlider.vue';
import { useI18n } from '@/services/i18n';
import { useTimer } from '@/composables/useTimer';

const props = defineProps({
  gain: { type: Number, default: 0 },
  displayName: { type: String, default: '' },
  disabled: { type: Boolean, default: false },
  // The record for the selected target has arrived. Until it has, `gain` is the
  // zeroed placeholder cleanup() leaves behind, so showing it would assert a flat
  // curve the target may not have.
  loaded: { type: Boolean, default: true },
  orientation: { type: String, default: 'vertical' },
  compact: { type: Boolean, default: false }
});

const emit = defineEmits(['update:gain', 'change']);

const isDragging = ref(false);

// A preset glides; a first record arriving is placed. The gains and `loaded` land in
// one tick, and a transition reads the style it lands in, so the glide comes
// back only once a frame has drawn the gains — else every open of the modal
// would raise all ten bands from the zeroed placeholder.
const placed = ref(false);

// A target switch reloads a band that already drew a curve. It holds that
// curve, disabled and unfigured, until the next record lands and the band glides
// to it — never the zeroed placeholder. A record slower than HOLD_MS is a target
// not answering, and the band hides as on a first load rather than keep showing
// the previous target's curve under the new tab.
const HOLD_MS = 1000;
const timer = useTimer();
const holding = ref(false);
let holdTimer = null;

const shownGain = ref(props.gain);
watch(() => props.gain, (gain) => {
  if (props.loaded) shownGain.value = gain;
});

watch(() => props.loaded, (loaded) => {
  timer.clear(holdTimer);
  if (loaded) shownGain.value = props.gain;
  if (!loaded && placed.value) {
    holding.value = true;
    holdTimer = timer.setTimeout(() => { holding.value = false; placed.value = false; }, HOLD_MS);
    return;
  }
  if (loaded && holding.value) {
    holding.value = false;
    return;
  }
  placed.value = false;
  if (loaded) requestAnimationFrame(() => requestAnimationFrame(() => { placed.value = props.loaded; }));
}, { immediate: true });

const gainValue = computed(() => props.gain);

const { formatNumber } = useI18n();
// +2,5 / -1,0 / 0,0: the sign on every non-zero gain, one decimal always.
const GAIN_FORMAT = { signDisplay: 'exceptZero', minimumFractionDigits: 1, maximumFractionDigits: 1 };

const sliderOrientation = computed(() => props.orientation === 'horizontal' ? 'horizontal' : 'vertical');

function handleGainInput(value) {
  emit('update:gain', value);
}

function handleGainChange(value) {
  // Only emit update:gain, not 'change' - handleDragEnd will emit 'change' once on release
  // This prevents duplicate 'change' events when RangeSlider fires both @change and @drag-end
  emit('update:gain', value);
}

function handleDragEnd() {
  isDragging.value = false;
  // Emit 'change' only once on drag end to trigger final API call
  emit('change', { field: 'gain', value: gainValue.value });
}
</script>

<style scoped>
.eq-band {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-02);
  max-width: 48px;
}

.eq-band.horizontal {
  flex-direction: row;
  min-width: unset;
  width: 100%;
  gap: var(--space-03);
  max-width: none;
}

.eq-band.compact {
  min-width: 48px;
}

.band-label {
  color: var(--color-text-secondary);
  text-align: center;
}

.eq-band.horizontal .band-label {
  min-width: 32px;
  text-align: right;
}

.gain-slider {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: opacity var(--transition-fast);
}

.gain-slider.pending {
  opacity: 0;
}

.eq-band:not(.horizontal) .gain-slider {
  min-height: 180px;
}

.eq-band.horizontal .gain-slider {
  flex: 1;
}

.gain-value {
  /* The band is sized by its widest child, so a reading growing from "+3.5" to
     "-10.0" moved every other band. Reserve the widest one in both layouts. */
  width: 5ch;
  color: var(--color-brand);
  text-align: center;
  transition: color var(--transition-fast);
}

.gain-value.dragging {
  color: var(--color-text-secondary);
}

.eq-band.horizontal .gain-value {
  text-align: left;
}

/* Mobile adjustments */
@media (max-aspect-ratio: 4/3) and (max-width: 1023px) {
  .eq-band:not(.horizontal) {
    min-width: 48px;
  }

  .gain-value {
    min-width: 48px;
  }
}
</style>
