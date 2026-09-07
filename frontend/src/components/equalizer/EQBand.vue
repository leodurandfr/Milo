<!-- frontend/src/components/equalizer/EQBand.vue -->
<!-- Individual EQ band control: band label and gain slider -->
<!-- The class names .gain-slider and .gain-value are read as anchors by
     tests/architecture/equalizerTargetScope.test.js, which pins that neither
     renders a figure before the store has loaded one. Renaming either is fine;
     update PRINTED_GAIN_CLASS / GAIN_SLIDER_CLASS there in the same commit. -->
<template>
  <div class="eq-band" :class="{ 'horizontal': orientation === 'horizontal', 'compact': compact }">
    <div class="band-label text-mono-small">{{ displayName }}</div>

    <div class="gain-slider" :class="{ 'pending': !loaded }">
      <RangeSlider
        :model-value="gainValue"
        :min="-15"
        :max="15"
        :step="0.5"
        :orientation="sliderOrientation"
        :disabled="disabled"
        :hide-inline-value="true"
        @input="handleGainInput"
        @change="handleGainChange"
        @drag-start="isDragging = true"
        @drag-end="handleDragEnd"
      />
    </div>

    <div class="gain-value text-mono-small" :class="{ 'dragging': isDragging }">
      {{ loaded ? `${gainValue > 0 ? '+' : ''}${gainValue.toFixed(1)}` : '—' }}
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue';
import RangeSlider from '@/components/ui/RangeSlider.vue';

const props = defineProps({
  id: { type: String, required: true },
  freq: { type: Number, required: true },
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

const gainValue = computed(() => props.gain);

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
@media (max-aspect-ratio: 4/3) {
  .eq-band:not(.horizontal) {
    min-width: 48px;
  }

  .gain-value {
    min-width: 48px;
  }
}
</style>
