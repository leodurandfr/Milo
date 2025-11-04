<!-- frontend/src/components/ui/RangeSlider.vue -->
<template>
  <div ref="sliderContainer" :class="['slider-container', orientation]" :style="cssVars">
    <input type="range" :class="['range-slider', orientation]" :min="min" :max="max" :step="step" :value="modelValue"
      @input="handleInput" @change="handleChange" @pointerdown="handlePointerDown" @pointerup="handlePointerUp"
      :disabled="disabled">

    <div v-if="orientation === 'horizontal' && !hideInlineValue" class="slider-value text-mono" :class="{ dragging: isDragging }">
      {{ modelValue }}{{ valueUnit }}
    </div>
  </div>
</template>

<script setup>
import { computed, ref, onMounted, onUnmounted } from 'vue';

const props = defineProps({
  modelValue: { type: Number, required: true },
  min: { type: Number, default: 0 },
  max: { type: Number, default: 100 },
  step: { type: Number, default: 1 },
  orientation: { type: String, default: 'horizontal' },
  disabled: { type: Boolean, default: false },
  valueUnit: { type: String, default: '' },
  hideInlineValue: { type: Boolean, default: false }
});

const emit = defineEmits(['update:modelValue', 'input', 'change', 'drag-start', 'drag-end']);

const isDragging = ref(false);
const sliderContainer = ref(null);
const containerSize = ref({ width: 0, height: 0 });

let resizeObserver = null;

onMounted(() => {
  if (sliderContainer.value) {
    // Initial size
    updateContainerSize();

    // Watch for size changes
    resizeObserver = new ResizeObserver(() => {
      updateContainerSize();
    });
    resizeObserver.observe(sliderContainer.value);
  }
});

onUnmounted(() => {
  if (resizeObserver) {
    resizeObserver.disconnect();
  }
});

function updateContainerSize() {
  if (sliderContainer.value) {
    const rect = sliderContainer.value.getBoundingClientRect();
    containerSize.value = { width: rect.width, height: rect.height };
  }
}

const percentage = computed(() => {
  const rawPercentage = ((props.modelValue - props.min) / (props.max - props.min)) * 100;

  if (props.orientation === 'horizontal') {
    // Thumb width is 62px
    const thumbWidth = 62;
    const containerWidth = containerSize.value.width || 400; // Fallback to reasonable default
    const thumbAdjustment = (thumbWidth / containerWidth) * 100;
    return rawPercentage * (100 - thumbAdjustment) / 100 + thumbAdjustment / 2;
  } else {
    // Thumb height is 62px
    const thumbHeight = 62;
    const containerHeight = containerSize.value.height || 260; // Fallback to reasonable default
    const thumbAdjustment = (thumbHeight / containerHeight) * 100;
    return rawPercentage * (100 - thumbAdjustment) / 100 + thumbAdjustment / 2;
  }
});

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

function handlePointerDown(event) {
  if (event.button !== 0 || props.disabled) return;
  isDragging.value = true;
  emit('drag-start');
}

function handlePointerUp() {
  if (isDragging.value) {
    isDragging.value = false;
    emit('drag-end');
  }
}
</script>

<style scoped>
.slider-container {
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
}

.slider-container.horizontal {
  width: 100%;
  height: 40px;
}

.slider-container.vertical {
  width: 40px;
  flex: 1;
  flex-direction: column;
}

.range-slider {
  -webkit-appearance: none;
  appearance: none;
  outline: none;
  cursor: pointer;
  border: none;
  border-radius: 20px;
  transition: opacity 300ms ease;
  pointer-events: none;
}

.range-slider.horizontal {
  width: 100%;
  height: 40px;
  background: linear-gradient(to right,
      #767C76 0%,
      #767C76 var(--progress),
      var(--color-background) var(--progress),
      var(--color-background) 100%);
}

.range-slider.vertical {
  width: 40px;
  min-height: 260px;
  flex: 1;
  writing-mode: vertical-lr;
  direction: rtl;
  background: linear-gradient(to top,
      #767C76 0%,
      #767C76 var(--progress),
      var(--color-background) var(--progress),
      var(--color-background) 100%);
}

.range-slider.horizontal::-webkit-slider-thumb {
  -webkit-appearance: none;
  appearance: none;
  width: 62px;
  height: 40px;
  border-radius: 20px;
  background: #FFFFFF;
  border: 2px solid #767C76;
  cursor: pointer;
  box-shadow: none;
  pointer-events: auto;
}

.range-slider.horizontal::-moz-range-thumb {
  width: 58px;
  height: 36px;
  border-radius: 20px;
  background: #FFFFFF;
  border: 2px solid #767C76;
  cursor: pointer;
  pointer-events: auto;
}

.range-slider.vertical::-webkit-slider-thumb {
  -webkit-appearance: none;
  appearance: none;
  width: 40px;
  height: 62px;
  border-radius: 20px;
  background: #FFFFFF;
  border: 2px solid #767C76;
  cursor: pointer;
  pointer-events: auto;
}

.range-slider.vertical::-moz-range-thumb {
  width: 36px;
  height: 58px;
  border-radius: 20px;
  background: #FFFFFF;
  border: 2px solid #767C76;
  cursor: pointer;
  pointer-events: auto;
}

.range-slider::-webkit-slider-track {
  background: transparent;
  border: none;
  pointer-events: none;
}

.range-slider::-moz-range-track {
  background: transparent;
  border: none;
  pointer-events: none;
}

.range-slider:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.range-slider:disabled::-webkit-slider-thumb,
.range-slider:disabled::-moz-range-thumb {
  cursor: not-allowed;
}

.slider-value {
  position: absolute;
  right: var(--space-04);
  color: var(--color-text-secondary);
  transition: color 300ms ease;
  pointer-events: none;
  z-index: 1;
}

.slider-value.dragging {
  color: var(--color-brand);
}
</style>