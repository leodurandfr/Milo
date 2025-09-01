<!-- frontend/src/components/ui/DoubleRangeSlider.vue - Reproduction exacte du style RangeSlider -->
<template>
  <div class="slider-container horizontal" :style="cssVars">
    <!-- Track principal avec gradient identique au RangeSlider -->
    <div 
      class="range-track"
      ref="track"
      @pointerdown="handleTrackClick"
    ></div>
    
    <!-- Thumb minimum -->
    <div 
      class="range-thumb thumb-min"
      :class="{ dragging: isDraggingMin }"
      :style="{ left: `${minPercentage}%` }"
      @pointerdown="startDrag($event, 'min')"
      @touchstart.prevent
    ></div>
    
    <!-- Thumb maximum -->
    <div 
      class="range-thumb thumb-max"
      :class="{ dragging: isDraggingMax }"
      :style="{ left: `${maxPercentage}%` }"
      @pointerdown="startDrag($event, 'max')"
      @touchstart.prevent
    ></div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue';

const props = defineProps({
  modelValue: {
    type: Object,
    required: true,
    validator: (value) => value && typeof value.min === 'number' && typeof value.max === 'number'
  },
  min: { type: Number, default: 0 },
  max: { type: Number, default: 100 },
  step: { type: Number, default: 1 },
  gap: { type: Number, default: 10 }
});

const emit = defineEmits(['update:modelValue', 'input', 'change']);

// État du drag
const isDraggingMin = ref(false);
const isDraggingMax = ref(false);
const track = ref(null);

// Calcul des pourcentages EXACT comme dans RangeSlider
const minPercentage = computed(() => {
  const rawPercentage = ((props.modelValue.min - props.min) / (props.max - props.min)) * 100;
  const thumbAdjustment = 15;
  return rawPercentage * (100 - thumbAdjustment) / 100 + thumbAdjustment / 2;
});

const maxPercentage = computed(() => {
  const rawPercentage = ((props.modelValue.max - props.min) / (props.max - props.min)) * 100;
  const thumbAdjustment = 15;
  return rawPercentage * (100 - thumbAdjustment) / 100 + thumbAdjustment / 2;
});

// Variables CSS pour le gradient
const cssVars = computed(() => ({
  '--progress-min': `${minPercentage.value}%`,
  '--progress-max': `${maxPercentage.value}%`
}));

// Helpers
function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function roundToStep(value) {
  return Math.round(value / props.step) * props.step;
}

function updateValues(newMin, newMax, triggerInput = true) {
  // Contraintes de base
  newMin = clamp(roundToStep(newMin), props.min, props.max);
  newMax = clamp(roundToStep(newMax), props.min, props.max);
  
  // Garantir le gap minimum
  if (newMax - newMin < props.gap) {
    if (isDraggingMin.value) {
      newMax = Math.min(props.max, newMin + props.gap);
    } else if (isDraggingMax.value) {
      newMin = Math.max(props.min, newMax - props.gap);
    }
  }
  
  const newValue = { min: newMin, max: newMax };
  
  if (newValue.min !== props.modelValue.min || newValue.max !== props.modelValue.max) {
    emit('update:modelValue', newValue);
    if (triggerInput) {
      emit('input', newValue);
    }
  }
}

// Gestion du drag
let dragType = null;
let startPosition = 0; // Position initiale du curseur
let startValue = 0; // Valeur initiale du thumb

function startDrag(event, type) {
  if (event.button !== 0) return;
  
  event.preventDefault();
  event.stopPropagation();
  dragType = type;
  
  // Mémoriser position initiale curseur et valeur initiale
  startPosition = event.clientX;
  startValue = type === 'min' ? props.modelValue.min : props.modelValue.max;
  
  if (type === 'min') {
    isDraggingMin.value = true;
  } else {
    isDraggingMax.value = true;
  }
  
  document.addEventListener('pointermove', handleDrag);
  document.addEventListener('pointerup', stopDrag);
  document.addEventListener('pointercancel', stopDrag);
}

function handleDrag(event) {
  if (!track.value || !dragType) return;
  
  const rect = track.value.getBoundingClientRect();
  
  // Calculer le delta en pixels depuis la position initiale
  const deltaX = event.clientX - startPosition;
  
  // Convertir le delta en valeur
  const deltaValue = (deltaX / rect.width) * (props.max - props.min);
  const newValue = startValue + deltaValue;
  
  if (dragType === 'min') {
    updateValues(newValue, props.modelValue.max, true);
  } else {
    updateValues(props.modelValue.min, newValue, true);
  }
}

function stopDrag() {
  isDraggingMin.value = false;
  isDraggingMax.value = false;
  dragType = null;
  
  document.removeEventListener('pointermove', handleDrag);
  document.removeEventListener('pointerup', stopDrag);
  document.removeEventListener('pointercancel', stopDrag);
}

function handleTrackClick(event) {
  if (!track.value) return;
  
  const rect = track.value.getBoundingClientRect();
  const percentage = (event.clientX - rect.left) / rect.width;
  const value = props.min + (percentage * (props.max - props.min));
  
  // Déterminer quel thumb est le plus proche
  const distToMin = Math.abs(value - props.modelValue.min);
  const distToMax = Math.abs(value - props.modelValue.max);
  
  if (distToMin < distToMax) {
    updateValues(value, props.modelValue.max, true);
  } else {
    updateValues(props.modelValue.min, value, true);
  }
}

onMounted(() => {
  updateValues(props.modelValue.min, props.modelValue.max, false);
});

onUnmounted(() => {
  document.removeEventListener('pointermove', handleDrag);
  document.removeEventListener('pointerup', stopDrag);
  document.removeEventListener('pointercancel', stopDrag);
});
</script>

<style scoped>
/* Container identique à RangeSlider */
.slider-container {
  display: flex;
  align-items: center;
  justify-content: center;
}

.slider-container.horizontal {
  width: 100%;
  height: 40px;
  position: relative;
}

/* Track principal avec gradient 3-sections identique au RangeSlider */
.range-track {
  width: 100%;
  height: 40px;
  border-radius: 20px;
  background: linear-gradient(to right, 
    var(--color-background) 0%, 
    var(--color-background) var(--progress-min), 
    #767C76 var(--progress-min), 
    #767C76 var(--progress-max), 
    var(--color-background) var(--progress-max), 
    var(--color-background) 100%);
  cursor: pointer;
}

/* Thumbs identiques au RangeSlider */
.range-thumb {
  position: absolute;
  top: 0;
  width: 62px;
  height: 40px;
  border-radius: 20px;
  background: #FFFFFF;
  border: 2px solid #767C76;
  cursor: pointer;
  transform: translateX(-50%);
  transition: transform var(--transition-fast);
}

.range-thumb:hover {
  /* Pas de scale au hover - garder juste la transition */
}

.range-thumb.dragging {
  cursor: grabbing;
  /* Pas de scale au drag non plus */
}

/* Z-index pour les thumbs */
.thumb-min {
  z-index: 2;
}

.thumb-max {
  z-index: 3;
}

.thumb-max.dragging {
  z-index: 4;
}
</style>