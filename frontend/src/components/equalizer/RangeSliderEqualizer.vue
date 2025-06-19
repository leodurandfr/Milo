<!-- frontend/src/components/equalizer/RangeSliderEqualizer.vue -->
<template>
  <div class="equalizer-slider">
    <label v-if="label" class="slider-label">
      {{ label }}
    </label>
    
    <RangeSlider
      :model-value="modelValue"
      :min="min"
      :max="max"
      :step="step"
      orientation="vertical"
      :disabled="disabled"
      @update:modelValue="$emit('update:modelValue', $event)"
      @input="handleInput"
      @change="handleChange"
    />
    
    <div v-if="showValue" class="value-display">
      {{ modelValue }}{{ unit }}
    </div>
  </div>
</template>

<script setup>
import RangeSlider from '../ui/RangeSlider.vue';

const props = defineProps({
  modelValue: {
    type: Number,
    required: true
  },
  min: {
    type: Number,
    default: 0
  },
  max: {
    type: Number,
    default: 100
  },
  step: {
    type: Number,
    default: 1
  },
  label: {
    type: String,
    default: ''
  },
  showValue: {
    type: Boolean,
    default: true
  },
  unit: {
    type: String,
    default: '%'
  },
  disabled: {
    type: Boolean,
    default: false
  }
});

const emit = defineEmits(['update:modelValue', 'input', 'change']);

function handleInput(value) {
  emit('input', value);
}

function handleChange(value) {
  emit('change', value);
}
</script>

<style scoped>
.equalizer-slider {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  height: 200px;
}

/* Label */
.slider-label {
  font-size: 12px;
  font-weight: 500;
  color: #333;
  text-align: center;
  white-space: nowrap;
}

/* Affichage de la valeur */
.value-display {
  font-size: 12px;
  font-weight: bold;
  color: #2196F3;
  text-align: center;
  white-space: nowrap;
}

/* Responsive */
@media (max-width: 600px) {
  .slider-label, .value-display {
    font-size: 11px;
  }
}
</style>