<!-- frontend/src/components/equalizer/RangeSliderEqualizer.vue - Version ULTRA simplifiée -->
<template>
  <div class="equalizer-slider">
    <div v-if="label" class="label">{{ label }}</div>
    
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
  height: 100%; /* CORRIGÉ : Revenir à height pour cohérence totale */
  min-height: 0;
}

.label {
  font-size: 12px;
  font-weight: 500;
  color: #333;
  text-align: center;
  flex-shrink: 0;
  line-height: 1.2;
}

/* Le RangeSlider prend maintenant directement l'espace disponible */

.value {
  font-size: 12px;
  font-weight: bold;
  color: #767C76;
  text-align: center;
  flex-shrink: 0;
  line-height: 1.2;
}

@media (max-width: 600px) {
  .label, .value {
    font-size: 11px;
  }
}
</style>