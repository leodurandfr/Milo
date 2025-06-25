<!-- frontend/src/components/ui/RangeSlider.vue - Version OPTIM complète -->
<template>
  <div :class="['slider-container', orientation]" :style="cssVars">
    <input 
      type="range" 
      :class="['range-slider', orientation]"
      :min="min" 
      :max="max" 
      :step="step" 
      :value="modelValue"
      @input="handleInput" 
      @change="handleChange" 
      :disabled="disabled"
    >
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

// Calcul du pourcentage ajusté pour le centre du thumb
const percentage = computed(() => {
  const rawPercentage = ((props.modelValue - props.min) / (props.max - props.min)) * 100;
  
  // Ajustement selon l'orientation
  if (props.orientation === 'horizontal') {
    // Thumb horizontal : 62px de large
    // Container typique : ~200px+ de large
    // Ajustement : ±15% aux extrémités
    const thumbAdjustment = 15;
    return rawPercentage * (100 - thumbAdjustment) / 100 + thumbAdjustment / 2;
  } else {
    // Thumb vertical : 62px de haut  
    // Container typique : ~280px+ de haut
    // Ajustement : ±11% aux extrémités
    const thumbAdjustment = 11;
    return rawPercentage * (100 - thumbAdjustment) / 100 + thumbAdjustment / 2;
  }
});

// Variables CSS
const cssVars = computed(() => ({
  '--progress': `${percentage.value}%`
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

<style scoped>
/* Container responsive - simple et propre */
.slider-container {
  display: flex;
  align-items: center;
  justify-content: center;
}

.slider-container.horizontal {
  width: 100%;
  height: 40px;
}

.slider-container.vertical {
  width: 40px;
  height: 100%;
  flex-direction: column;
}

/* Base slider */
.range-slider {
  -webkit-appearance: none;
  appearance: none;
  outline: none;
  cursor: pointer;
  border: none;
  border-radius: 20px;
}

/* Slider horizontal */
.range-slider.horizontal {
  width: 100%;
  height: 40px;
  background: linear-gradient(to right, 
    #767C76 0%, 
    #767C76 var(--progress), 
    #F2F2F2 var(--progress), 
    #F2F2F2 100%);
}

/* Slider vertical - hérite de la hauteur du container */
.range-slider.vertical {
  width: 40px;
  height: inherit; /* Au lieu de 100% */
  writing-mode: vertical-lr;
  direction: rtl;
  background: linear-gradient(to top, 
    #767C76 0%, 
    #767C76 var(--progress), 
    #F2F2F2 var(--progress), 
    #F2F2F2 100%);
}

/* Thumb horizontal */
.range-slider.horizontal::-webkit-slider-thumb {
  -webkit-appearance: none;
  appearance: none;
  width: 62px;
  height: 40px;
  border-radius: 20px;
  background: #FFFFFF;
  border: 2px solid #767C76;
  cursor: pointer;
}

.range-slider.horizontal::-moz-range-thumb {
  width: 58px; /* -4px pour border */
  height: 36px; /* -4px pour border */
  border-radius: 20px;
  background: #FFFFFF;
  border: 2px solid #767C76;
  cursor: pointer;
}

/* Thumb vertical - dimensions inversées */
.range-slider.vertical::-webkit-slider-thumb {
  -webkit-appearance: none;
  appearance: none;
  width: 40px;  /* Inversé */
  height: 62px; /* Inversé */
  border-radius: 20px;
  background: #FFFFFF;
  border: 2px solid #767C76;
  cursor: pointer;
}

.range-slider.vertical::-moz-range-thumb {
  width: 36px;  /* Inversé -4px pour border */
  height: 58px; /* Inversé -4px pour border */
  border-radius: 20px;
  background: #FFFFFF;
  border: 2px solid #767C76;
  cursor: pointer;
}

/* Track Firefox (pour cohérence) */
.range-slider::-moz-range-track {
  background: transparent;
  border: none;
}

/* États disabled */
.range-slider:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.range-slider:disabled::-webkit-slider-thumb,
.range-slider:disabled::-moz-range-thumb {
  background: #ccc;
  border-color: #999;
  cursor: not-allowed;
}
</style>