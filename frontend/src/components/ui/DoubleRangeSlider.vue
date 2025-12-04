<!-- frontend/src/components/ui/DoubleRangeSlider.vue - Full embedded values -->
<template>
  <div class="double-range-slider" :style="cssVars">
    <!-- Main track with gradient -->
    <div
      class="range-track"
      ref="track"
    ></div>
    
    <!-- Minimum thumb -->
    <div 
      class="range-thumb thumb-min"
      :class="{ dragging: isDraggingMin }"
      :style="{ left: minPosition }"
      @pointerdown="startDrag($event, 'min')"
      @touchstart.prevent
    ></div>
    
    <!-- Maximum thumb -->
    <div 
      class="range-thumb thumb-max"
      :class="{ dragging: isDraggingMax }"
      :style="{ left: maxPosition }"
      @pointerdown="startDrag($event, 'max')"
      @touchstart.prevent
    ></div>
    
    <!-- Minimum value (left) -->
    <div class="slider-value-min text-mono" :class="{ dragging: isDraggingMin }">
      {{ modelValue.min }}{{ valueUnit }}
    </div>
    
    <!-- Maximum value (right) -->
    <div class="slider-value-max text-mono" :class="{ dragging: isDraggingMax }">
      {{ modelValue.max }}{{ valueUnit }}
    </div>
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
  gap: { type: Number, default: 10 },
  valueUnit: { type: String, default: '' }
});

const emit = defineEmits(['update:modelValue', 'input', 'change', 'drag-start', 'drag-end']);

// Drag state
const isDraggingMin = ref(false);
const isDraggingMax = ref(false);
const track = ref(null);
const trackWidth = ref(0);

let resizeObserver = null;

// Pixel positions (clean)
const minPosition = computed(() => {
  const percentage = (props.modelValue.min - props.min) / (props.max - props.min);
  return `calc(31px + ${percentage} * (100% - 62px))`;
});

const maxPosition = computed(() => {
  const percentage = (props.modelValue.max - props.min) / (props.max - props.min);
  return `calc(31px + ${percentage} * (100% - 62px))`;
});

// Percentages for the gradient - dynamic calculation based on actual width
const minPercentageForGradient = computed(() => {
  const rawPercentage = ((props.modelValue.min - props.min) / (props.max - props.min)) * 100;
  // Thumb width is 62px
  const thumbWidth = 62;
  const containerWidth = trackWidth.value || 400; // Fallback to reasonable default
  const thumbAdjustment = (thumbWidth / containerWidth) * 100;
  return rawPercentage * (100 - thumbAdjustment) / 100 + thumbAdjustment / 2;
});

const maxPercentageForGradient = computed(() => {
  const rawPercentage = ((props.modelValue.max - props.min) / (props.max - props.min)) * 100;
  // Thumb width is 62px
  const thumbWidth = 62;
  const containerWidth = trackWidth.value || 400; // Fallback to reasonable default
  const thumbAdjustment = (thumbWidth / containerWidth) * 100;
  return rawPercentage * (100 - thumbAdjustment) / 100 + thumbAdjustment / 2;
});

// CSS variables for the gradient - uses the same logic as RangeSlider
const cssVars = computed(() => ({
  '--progress-min': `${minPercentageForGradient.value}%`,
  '--progress-max': `${maxPercentageForGradient.value}%`
}));

// Helpers
function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function roundToStep(value) {
  return Math.round(value / props.step) * props.step;
}

function updateValues(newMin, newMax, triggerInput = true) {
  newMin = clamp(roundToStep(newMin), props.min, props.max);
  newMax = clamp(roundToStep(newMax), props.min, props.max);
  
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

// Drag handling
let dragType = null;
let thumbOffset = 0;

function startDrag(event, type) {
  if (event.button !== 0) return;
  
  event.preventDefault();
  event.stopPropagation();
  dragType = type;
  
  if (!track.value) return;
  
  const rect = track.value.getBoundingClientRect();
  const clickX = event.clientX;
  
  // Current center position of the thumb with the new calculation
  const currentPercentage = type === 'min' 
    ? (props.modelValue.min - props.min) / (props.max - props.min)
    : (props.modelValue.max - props.min) / (props.max - props.min);
    
  // Thumb center position: 31px + percentage * (usable width)
  const usableWidth = rect.width - 62; // width - thumb width
  const thumbCenterX = rect.left + 31 + (currentPercentage * usableWidth);
  
  // Offset = difference between where we click and the thumb center
  thumbOffset = clickX - thumbCenterX;
  
  if (type === 'min') {
    isDraggingMin.value = true;
    emit('drag-start', 'min');
  } else {
    isDraggingMax.value = true;
    emit('drag-start', 'max');
  }
  
  document.addEventListener('pointermove', handleDrag);
  document.addEventListener('pointerup', stopDrag);
  document.addEventListener('pointercancel', stopDrag);
}

function handleDrag(event) {
  if (!track.value || !dragType) return;
  
  const rect = track.value.getBoundingClientRect();
  const correctedX = event.clientX - thumbOffset;
  
  // Calculate position in the usable area (31px to width-31px)
  const usableWidth = rect.width - 62; // width - thumb width  
  const positionInUsableArea = correctedX - rect.left - 31; // position - start of usable area
  const percentage = clamp(positionInUsableArea / usableWidth, 0, 1);
  const value = props.min + (percentage * (props.max - props.min));
  
  if (dragType === 'min') {
    updateValues(value, props.modelValue.max, true);
  } else {
    updateValues(props.modelValue.min, value, true);
  }
}

function stopDrag() {
  const wasMin = isDraggingMin.value;
  const wasMax = isDraggingMax.value;

  isDraggingMin.value = false;
  isDraggingMax.value = false;
  dragType = null;

  if (wasMin) {
    emit('drag-end', 'min');
  } else if (wasMax) {
    emit('drag-end', 'max');
  }

  document.removeEventListener('pointermove', handleDrag);
  document.removeEventListener('pointerup', stopDrag);
  document.removeEventListener('pointercancel', stopDrag);
}

onMounted(() => {
  updateValues(props.modelValue.min, props.modelValue.max, false);

  // Watch for size changes
  if (track.value) {
    // Initial width
    updateTrackWidth();

    resizeObserver = new ResizeObserver(() => {
      updateTrackWidth();
    });
    resizeObserver.observe(track.value);
  }
});

onUnmounted(() => {
  document.removeEventListener('pointermove', handleDrag);
  document.removeEventListener('pointerup', stopDrag);
  document.removeEventListener('pointercancel', stopDrag);

  if (resizeObserver) {
    resizeObserver.disconnect();
  }
});

function updateTrackWidth() {
  if (track.value) {
    const rect = track.value.getBoundingClientRect();
    trackWidth.value = rect.width;
  }
}
</script>

<style scoped>
/* Container */
.double-range-slider {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 40px;
}

/* Track with gradient identical to RangeSlider */
.range-track {
  width: 100%;
  height: 40px;
  border-radius: 20px;
  background: linear-gradient(to right,
    var(--color-background) 0%,
    var(--color-background) var(--progress-min),
    var(--color-text-secondary) var(--progress-min),
    var(--color-text-secondary) var(--progress-max),
    var(--color-background) var(--progress-max),
    var(--color-background) 100%);
  pointer-events: none;
}

/* Thumbs identical to RangeSlider */
.range-thumb {
  position: absolute;
  top: 0;
  width: 62px;
  height: 40px;
  border-radius: 20px;
  background: #FFFFFF;
  border: 2px solid var(--color-text-secondary);
  cursor: pointer;
  transform: translateX(-50%);
}

/* Z-index for thumbs */
.thumb-min {
  z-index: 2;
}

.thumb-max {
  z-index: 3;
}

.thumb-max.dragging {
  z-index: 4;
}

/* Minimum value on the left - above the thumbs */
.slider-value-min {
  position: absolute;
  left: var(--space-04);
  color: var(--color-text-secondary);
  transition: color var(--transition-fast);
  pointer-events: none;
  z-index: 5;
}

.slider-value-min.dragging {
  color: var(--color-brand);
}

/* Maximum value on the right - above the thumbs */
.slider-value-max {
  position: absolute;
  right: var(--space-04);
  color: var(--color-text-secondary);
  transition: color var(--transition-fast);
  pointer-events: none;
  z-index: 5;
}

.slider-value-max.dragging {
  color: var(--color-brand);
}
</style>