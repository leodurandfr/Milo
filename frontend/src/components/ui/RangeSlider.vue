<!-- frontend/src/components/ui/RangeSlider.vue - Version simplifiée -->
<template>
  <input
    type="range"
    :min="min"
    :max="max"
    :step="step"
    :value="displayValue"
    @input="handleInput"
    @change="handleChange"
    :disabled="disabled"
    :class="['range-slider', `orientation-${orientation}`]"
  >
</template>

<script setup>
import { ref, computed } from 'vue';

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
  orientation: {
    type: String,
    default: 'horizontal', // 'horizontal' | 'vertical'
    validator: value => ['horizontal', 'vertical'].includes(value)
  },
  disabled: {
    type: Boolean,
    default: false
  }
});

const emit = defineEmits(['update:modelValue', 'input', 'change']);

// État local pour feedback immédiat
const localValue = ref(null);

// Valeur affichée avec feedback immédiat
const displayValue = computed(() => 
  localValue.value !== null ? localValue.value : props.modelValue
);

// Gestion des événements
function handleInput(event) {
  const newValue = parseInt(event.target.value);
  
  // Feedback immédiat
  localValue.value = newValue;
  
  // Émission pour mise à jour temps réel
  emit('input', newValue);
  emit('update:modelValue', newValue);
}

function handleChange(event) {
  const newValue = parseInt(event.target.value);
  
  // Nettoyer le feedback local
  localValue.value = null;
  
  // Émission finale
  emit('change', newValue);
  emit('update:modelValue', newValue);
}
</script>

<style>
/* Range slider uniforme - Même style partout (pas de scoped) */
.range-slider {
  outline: none !important;
  appearance: none !important;
  border: none !important;
  cursor: pointer !important;
  background: #ddd !important;
  transition: background-color 0.2s !important;
}

/* Horizontal */
.range-slider.orientation-horizontal {
  width: 100% !important;
  height: 6px !important;
}

/* Vertical */
.range-slider.orientation-vertical {
  writing-mode: bt-lr !important; /* IE */
  -webkit-appearance: slider-vertical !important; /* WebKit */
  width: 6px !important;
  height: 150px !important;
}

/* États hover et disabled - IDENTIQUES pour les deux orientations */
.range-slider:hover:not(:disabled) {
  background: #ccc !important;
}

.range-slider:disabled {
  opacity: 0.5 !important;
  cursor: not-allowed !important;
}

/* Thumb WebKit - IDENTIQUE pour horizontal et vertical */
.range-slider::-webkit-slider-thumb {
  appearance: none !important;
  width: 18px !important;
  height: 18px !important;
  background: #2196F3 !important;
  border-radius: 50% !important;
  cursor: pointer !important;
  transition: background-color 0.2s, transform 0.1s !important;
  border: none !important;
}

.range-slider::-webkit-slider-thumb:hover {
  background: #1976D2 !important;
  transform: scale(1.1) !important;
}

.range-slider:disabled::-webkit-slider-thumb {
  cursor: not-allowed !important;
  transform: none !important;
  background: #999 !important;
}

/* Thumb Firefox - IDENTIQUE pour horizontal et vertical */
.range-slider::-moz-range-thumb {
  width: 18px !important;
  height: 18px !important;
  background: #2196F3 !important;
  border-radius: 50% !important;
  cursor: pointer !important;
  border: none !important;
  transition: background-color 0.2s, transform 0.1s !important;
}

.range-slider::-moz-range-thumb:hover {
  background: #1976D2 !important;
  transform: scale(1.1) !important;
}

.range-slider:disabled::-moz-range-thumb {
  cursor: not-allowed !important;
  transform: none !important;
  background: #999 !important;
}

/* Track Firefox - UNIFORME */
.range-slider::-moz-range-track {
  height: 6px !important;
  background: #ddd !important;
  border: none !important;
  border-radius: 3px !important;
}
</style>