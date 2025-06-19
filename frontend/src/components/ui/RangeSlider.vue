<!-- frontend/src/components/ui/RangeSlider.vue -->
<template>
  <div :class="['range-slider', `orientation-${orientation}`]">
    <label v-if="label" :for="inputId" class="slider-label">
      {{ label }}
    </label>
    
    <div class="slider-container">
      <input
        :id="inputId"
        type="range"
        :min="min"
        :max="max"
        :step="step"
        :value="displayValue"
        @input="handleInput"
        @change="handleChange"
        :disabled="disabled"
        :class="['slider-input', `orientation-${orientation}`]"
      >
      
      <div v-if="showValue" class="value-display">
        {{ formatValue(displayValue) }}{{ unit }}
      </div>
    </div>
  </div>
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
  label: {
    type: String,
    default: ''
  },
  orientation: {
    type: String,
    default: 'horizontal', // 'horizontal' | 'vertical'
    validator: value => ['horizontal', 'vertical'].includes(value)
  },
  showValue: {
    type: Boolean,
    default: true
  },
  unit: {
    type: String,
    default: ''
  },
  disabled: {
    type: Boolean,
    default: false
  },
  formatter: {
    type: Function,
    default: null
  }
});

const emit = defineEmits(['update:modelValue', 'input', 'change']);

// État local pour feedback immédiat
const localValue = ref(null);

// ID unique pour l'accessibilité
const inputId = computed(() => 
  `range-slider-${Math.random().toString(36).substr(2, 9)}`
);

// Valeur affichée avec feedback immédiat
const displayValue = computed(() => 
  localValue.value !== null ? localValue.value : props.modelValue
);

// Formatage de la valeur
function formatValue(value) {
  if (props.formatter) {
    return props.formatter(value);
  }
  return value;
}

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

<style scoped>
/* Conteneur principal */
.range-slider {
  display: flex;
  align-items: center;
  gap: 8px;
}

.range-slider.orientation-vertical {
  flex-direction: column;
  align-items: center;
  height: 200px;
}

.range-slider.orientation-horizontal {
  flex-direction: row;
  width: 100%;
}

/* Label */
.slider-label {
  font-size: 12px;
  font-weight: 500;
  color: #333;
  white-space: nowrap;
}

.orientation-vertical .slider-label {
  writing-mode: horizontal-tb;
  text-align: center;
  margin-bottom: 8px;
}

/* Conteneur du slider */
.slider-container {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
}

.orientation-vertical .slider-container {
  flex-direction: column;
  height: 100%;
  justify-content: center;
}

/* Input range */
.slider-input {
  flex: 1;
  height: 6px;
  background: #ddd;
  outline: none;
  appearance: none;
  border: none;
  cursor: pointer;
  transition: background-color 0.2s;
}

.slider-input.orientation-vertical {
  writing-mode: bt-lr; /* IE */
  -webkit-appearance: slider-vertical; /* WebKit */
  width: 6px;
  height: 150px;
  flex: 1;
}

.slider-input:hover:not(:disabled) {
  background: #ccc;
}

.slider-input:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* Thumb pour WebKit */
.slider-input::-webkit-slider-thumb {
  appearance: none;
  width: 18px;
  height: 18px;
  background: #2196F3;
  border-radius: 50%;
  cursor: pointer;
  transition: background-color 0.2s, transform 0.1s;
  border: none;
}

.slider-input::-webkit-slider-thumb:hover {
  background: #1976D2;
  transform: scale(1.1);
}

.slider-input:disabled::-webkit-slider-thumb {
  cursor: not-allowed;
  transform: none;
}

/* Thumb pour Firefox */
.slider-input::-moz-range-thumb {
  width: 18px;
  height: 18px;
  background: #2196F3;
  border-radius: 50%;
  cursor: pointer;
  border: none;
  transition: background-color 0.2s, transform 0.1s;
}

.slider-input::-moz-range-thumb:hover {
  background: #1976D2;
  transform: scale(1.1);
}

.slider-input:disabled::-moz-range-thumb {
  cursor: not-allowed;
  transform: none;
}

/* Track pour Firefox */
.slider-input::-moz-range-track {
  height: 6px;
  background: #ddd;
  border: none;
  border-radius: 3px;
}

/* Affichage de la valeur */
.value-display {
  font-size: 12px;
  font-weight: bold;
  color: #2196F3;
  min-width: 40px;
  text-align: center;
  white-space: nowrap;
}

.orientation-vertical .value-display {
  writing-mode: horizontal-tb;
  margin-top: 8px;
}

/* Responsive */
@media (max-width: 600px) {
  .range-slider.orientation-horizontal {
    gap: 6px;
  }
  
  .slider-label {
    font-size: 11px;
  }
  
  .value-display {
    font-size: 11px;
    min-width: 35px;
  }
}
</style>