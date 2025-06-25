<!-- frontend/src/components/equalizer/RangeSliderEqualizer.vue - Version OPTIM corrigée -->
<template>
  <div class="equalizer-slider">
    <div v-if="label" class="label">{{ label }}</div>
    
    <div class="slider-wrapper">
      <RangeSlider
        :model-value="modelValue"
        :min="min"
        :max="max"
        :step="step"
        orientation="vertical"
        :disabled="disabled"
        @update:modelValue="$emit('update:modelValue', $event)"
        @input="$emit('input', $event)"
        @change="$emit('change', $event)"
      />
    </div>
    
    <div v-if="showValue" class="value">{{ modelValue }}{{ unit }}</div>
  </div>
</template>

<script setup>
import RangeSlider from '../ui/RangeSlider.vue';

defineProps({
  modelValue: { type: Number, required: true },
  min: { type: Number, default: 0 },
  max: { type: Number, default: 100 },
  step: { type: Number, default: 1 },
  label: { type: String, default: '' },
  showValue: { type: Boolean, default: true },
  unit: { type: String, default: '%' },
  disabled: { type: Boolean, default: false }
});

defineEmits(['update:modelValue', 'input', 'change']);
</script>

<style scoped>
.equalizer-slider {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  height: 100%; /* S'adapte au parent au lieu de 280px fixe */
}

.label {
  font-size: 12px;
  font-weight: 500;
  color: #333;
  text-align: center;
  flex-shrink: 0; /* Ne se réduit jamais */
}

.slider-wrapper {
  flex: 1; /* Prend tout l'espace restant */
  width: 40px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.value {
  font-size: 12px;
  font-weight: bold;
  color: #767C76;
  text-align: center;
  flex-shrink: 0; /* Ne se réduit jamais */
}

@media (max-width: 600px) {
  .label, .value {
    font-size: 11px;
  }
}
</style>