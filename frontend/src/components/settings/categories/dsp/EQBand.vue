<!-- frontend/src/components/settings/categories/dsp/EQBand.vue -->
<!-- Individual parametric EQ band control with frequency, gain, Q, and type -->
<template>
  <div class="eq-band" :class="{ 'horizontal': orientation === 'horizontal', 'compact': compact }">
    <!-- Frequency label -->
    <div class="band-label text-mono">{{ displayName }}</div>

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
    <div class="gain-value text-mono" :class="{ 'dragging': isDragging, 'positive': gainValue > 0, 'negative': gainValue < 0 }">
      {{ gainValue > 0 ? '+' : '' }}{{ gainValue.toFixed(1) }} dB
    </div>

    <!-- Q and Type controls (shown in expanded mode) -->
    <div v-if="showAdvanced" class="advanced-controls">
      <div class="q-control">
        <span class="control-label text-mono">Q</span>
        <RangeSlider
          :model-value="qValue"
          :min="0.1"
          :max="10"
          :step="0.1"
          orientation="horizontal"
          :disabled="disabled"
          :hide-inline-value="true"
          @input="handleQInput"
          @change="handleQChange"
        />
        <span class="control-value text-mono">{{ qValue.toFixed(1) }}</span>
      </div>

      <div class="type-control">
        <Dropdown
          :model-value="filterType"
          :options="filterTypeOptions"
          variant="minimal"
          :disabled="disabled"
          @update:modelValue="handleTypeChange"
        />
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue';
import RangeSlider from '@/components/ui/RangeSlider.vue';
import Dropdown from '@/components/ui/Dropdown.vue';

const props = defineProps({
  id: { type: String, required: true },
  freq: { type: Number, required: true },
  gain: { type: Number, default: 0 },
  q: { type: Number, default: 1.0 },
  type: { type: String, default: 'Peaking' },
  displayName: { type: String, default: '' },
  disabled: { type: Boolean, default: false },
  orientation: { type: String, default: 'vertical' },
  showAdvanced: { type: Boolean, default: false },
  compact: { type: Boolean, default: false }
});

const emit = defineEmits(['update:gain', 'update:q', 'update:type', 'change']);

const isDragging = ref(false);

// Local values that sync with props
const gainValue = computed(() => props.gain);
const qValue = computed(() => props.q);
const filterType = computed(() => props.type);

// Slider orientation based on overall orientation
const sliderOrientation = computed(() => props.orientation === 'horizontal' ? 'horizontal' : 'vertical');

// Filter type options
const filterTypeOptions = [
  { value: 'Peaking', label: 'Peak' },
  { value: 'Lowshelf', label: 'Low Shelf' },
  { value: 'Highshelf', label: 'High Shelf' },
  { value: 'Lowpass', label: 'Low Pass' },
  { value: 'Highpass', label: 'High Pass' },
  { value: 'Notch', label: 'Notch' }
];

// === GAIN HANDLERS ===
function handleGainInput(value) {
  emit('update:gain', value);
}

function handleGainChange(value) {
  emit('update:gain', value);
  emit('change', { field: 'gain', value });
}

function handleDragEnd() {
  isDragging.value = false;
  emit('change', { field: 'gain', value: gainValue.value });
}

// === Q HANDLERS ===
function handleQInput(value) {
  emit('update:q', value);
}

function handleQChange(value) {
  emit('update:q', value);
  emit('change', { field: 'q', value });
}

// === TYPE HANDLER ===
function handleTypeChange(value) {
  emit('update:type', value);
  emit('change', { field: 'type', value });
}
</script>

<style scoped>
.eq-band {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-02);
  min-width: 56px;
}

.eq-band.horizontal {
  flex-direction: row;
  min-width: unset;
  width: 100%;
  gap: var(--space-03);
}

.eq-band.compact {
  min-width: 48px;
}

.band-label {
  color: var(--color-text-secondary);
  text-align: center;
  font-size: 13px;
}

.eq-band.horizontal .band-label {
  min-width: 48px;
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
  font-size: 12px;
  text-align: center;
  min-width: 56px;
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
  min-width: 64px;
  text-align: left;
}

/* Advanced controls */
.advanced-controls {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
  width: 100%;
  margin-top: var(--space-02);
  padding-top: var(--space-02);
  border-top: 1px solid var(--color-border);
}

.q-control {
  display: flex;
  align-items: center;
  gap: var(--space-02);
}

.control-label {
  color: var(--color-text-light);
  font-size: 11px;
  min-width: 16px;
}

.control-value {
  color: var(--color-text-secondary);
  font-size: 11px;
  min-width: 32px;
  text-align: right;
}

.type-control {
  width: 100%;
}

/* Mobile adjustments */
@media (max-aspect-ratio: 4/3) {
  .eq-band:not(.horizontal) {
    min-width: 48px;
  }

  .gain-value {
    font-size: 11px;
    min-width: 48px;
  }

  .band-label {
    font-size: 12px;
  }
}
</style>
