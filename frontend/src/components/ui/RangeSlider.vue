<!-- frontend/src/components/ui/RangeSlider.vue - Version OPTIM basée sur React -->
<template>
  <div :class="['slider-container', orientation]" :style="cssVars">
    <input type="range" :class="['range-slider', orientation]" :min="min" :max="max" :step="step" :value="modelValue"
      @input="handleInput" @change="handleChange" :disabled="disabled">
  </div>
</template>

<script setup>
import { computed } from 'vue';

const props = defineProps({
  modelValue: { type: Number, required: true },
  min: { type: Number, default: 0 },
  max: { type: Number, default: 100 },
  step: { type: Number, default: 1 },
  orientation: { type: String, default: 'horizontal' },
  disabled: { type: Boolean, default: false }
});

const emit = defineEmits(['update:modelValue', 'input', 'change']);

// Calcul du pourcentage pour la track active (ajusté pour le thumb)
const percentage = computed(() => {
  const rawPercentage = ((props.modelValue - props.min) / (props.max - props.min)) * 100;
  
  // Ajustement pour que la track s'arrête au centre du thumb
  // Thumb width = 62px, Slider width = 220px
  const thumbWidthPercentage = (62 / 220) * 100; // ~28%
  
  return rawPercentage * (100 - thumbWidthPercentage) / 100 + thumbWidthPercentage / 2;
});

// Variables CSS injectées
const cssVars = computed(() => ({
  '--fill-percentage': `${percentage.value}%`
}));

function handleInput(event) {
  const value = parseInt(event.target.value);
  emit('input', value);
  emit('update:modelValue', value);
}

function handleChange(event) {
  const value = parseInt(event.target.value);
  emit('change', value);
  emit('update:modelValue', value);
}
</script>

<style>
.slider-container {
  display: inline-block;
}

.slider-container.vertical {
  height: 220px;
  width: 30px;
  display: grid;
  justify-content: center;
  align-items: center;
}

.range-slider {
  -webkit-appearance: none;
  appearance: none;
  width: 220px;
  height: 40px;
  border-radius: 99px;
  outline: none;
  cursor: pointer;
  background: linear-gradient(to right,
      #767C76 0%,
      #767C76 var(--fill-percentage),
      #F2F2F2 var(--fill-percentage),
      #F2F2F2 100%);
}

.range-slider.horizontal {}

.range-slider.vertical {
  transform: rotate(-90deg);
  transform-origin: center;
}

.range-slider:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* Thumb WebKit */
.range-slider::-webkit-slider-thumb {
  -webkit-appearance: none;
  appearance: none;
  width: 62px;
  height: 40px;
  border-radius: 24px;
  background: #FFFFFF;
  cursor: pointer;
  border: 2px solid #767C76;
}

.range-slider:disabled::-webkit-slider-thumb {
  background: #ccc;
  border-color: #999;
  cursor: not-allowed;
}
</style>