<!-- frontend/src/components/equalizer/RangeSliderEqualizer.vue - Version avec orientation responsive -->
<template>
  <div class="equalizer-slider" :class="{ 'horizontal': orientation === 'horizontal' }">
    <div v-if="label" class="label text-mono">{{ label }}</div>

    <RangeSlider :model-value="modelValue" :min="min" :max="max" :step="step" :orientation="orientation"
      :disabled="disabled" @update:modelValue="$emit('update:modelValue', $event)" @input="$emit('input', $event)"
      @change="$emit('change', $event)" />

    <div v-if="showValue" class="value text-mono">{{ modelValue }}{{ unit }}</div>
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
  disabled: { type: Boolean, default: false },
  orientation: { type: String, default: 'vertical' }
});

defineEmits(['update:modelValue', 'input', 'change']);
</script>

<style scoped>
.equalizer-slider {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  height: 100%;
  /* CORRIGÉ : Revenir à height pour cohérence totale */
  min-height: 0;
}

.label {
  text-align: center;
  color: var(--color-text-secondary);
  width: 40px;
}

.value {
  color: var(--color-text-secondary);
  width: 40px;

}

/* Responsive */
@media (max-aspect-ratio: 4/3) {
  .equalizer-slider {
    flex-direction: row;
    align-items: center;
    width: 100%;
    height: auto;
    gap: var(--space-03);
  }




}
</style>