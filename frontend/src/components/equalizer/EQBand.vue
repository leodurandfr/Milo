<!-- frontend/src/components/equalizer/EQBand.vue -->
<!-- Individual EQ band control: band label and gain slider -->
<template>
  <div class="eq-band" :class="{ 'horizontal': orientation === 'horizontal', 'compact': compact }">
    <!-- Frequency label -->
    <div class="band-label text-mono-small">{{ displayName }}</div>

    <!-- Gain slider (vertical or horizontal) -->
    <div class="gain-slider">
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

    <!-- Gain value display -->
    <div class="gain-value text-mono-small" :class="{ 'dragging': isDragging, 'positive': gainValue > 0, 'negative': gainValue < 0 }">
      {{ gainValue > 0 ? '+' : '' }}{{ gainValue.toFixed(1) }}
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
  orientation: { type: String, default: 'vertical' },
  compact: { type: Boolean, default: false }
});

const emit = defineEmits(['update:gain', 'change']);

const isDragging = ref(false);

// Local values that sync with props
const gainValue = computed(() => props.gain);

// Slider orientation based on overall orientation
const sliderOrientation = computed(() => props.orientation === 'horizontal' ? 'horizontal' : 'vertical');

// === GAIN HANDLERS ===
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
  max-width: 40px;
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
}

.eq-band:not(.horizontal) .gain-slider {
  min-height: 180px;
}

.eq-band.horizontal .gain-slider {
  flex: 1;
}

.gain-value {
  color: var(--color-text-secondary);
  text-align: center;
  transition: color var(--transition-fast);
}

.gain-value.dragging {
  color: var(--color-brand);
}

.gain-value.positive {
  color: var(--color-success);
}

.gain-value.negative {
  color: var(--color-warning);
}

.gain-value.dragging.positive,
.gain-value.dragging.negative {
  color: var(--color-brand);
}

.eq-band.horizontal .gain-value {
  min-width: 32px;
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
