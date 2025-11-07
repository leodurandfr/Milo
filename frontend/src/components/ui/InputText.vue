<!-- frontend/src/components/ui/InputText.vue -->
<template>
  <input
    ref="inputRef"
    :type="type"
    :value="modelValue"
    :placeholder="placeholder"
    :disabled="disabled"
    :maxlength="maxlength"
    :class="inputClass"
    @input="handleInput"
    @focus="handleFocus"
    @blur="handleBlur"
  />
</template>

<script setup>
import { ref, computed } from 'vue';
import { useVirtualKeyboard } from '@/composables/useVirtualKeyboard';
import { useHardwareConfig } from '@/composables/useHardwareConfig';

const props = defineProps({
  modelValue: {
    type: String,
    default: ''
  },
  placeholder: {
    type: String,
    default: ''
  },
  type: {
    type: String,
    default: 'text'
  },
  disabled: {
    type: Boolean,
    default: false
  },
  maxlength: {
    type: Number,
    default: undefined
  },
  inputClass: {
    type: String,
    default: 'text-body'
  }
});

const emit = defineEmits(['update:modelValue', 'focus', 'blur']);

const inputRef = ref(null);
const keyboard = useVirtualKeyboard();
const { screenResolution } = useHardwareConfig();

// Detect if the resolution matches (to show the virtual keyboard)
const shouldShowKeyboard = computed(() => {
  // Force mode via URL parameter (for development/testing)
  const urlParams = new URLSearchParams(window.location.search);
  if (urlParams.get('virtualKeyboard') === 'true') {
    console.log('[InputText] Force mode enabled via URL');
    return true;
  }

  const configuredResolution = screenResolution.value;
  const currentWidth = window.innerWidth;
  const currentHeight = window.innerHeight;

  const configWidth = configuredResolution?.width;
  const configHeight = configuredResolution?.height;

  console.log('[InputText] Resolution check:', {
    configWidth,
    configHeight,
    browserWidth: currentWidth,
    browserHeight: currentHeight
  });

  // If no valid resolution configured, no virtual keyboard
  if (!configWidth || !configHeight) {
    console.log('[InputText] ❌ No valid resolution configured');
    return false;
  }

  // Check if resolutions match exactly
  const matches = currentWidth === configWidth && currentHeight === configHeight;

  console.log(`[InputText] Resolution ${matches ? '✅ MATCH' : '❌ NO MATCH'} (${currentWidth}x${currentHeight} vs ${configWidth}x${configHeight})`);

  return matches;
});

function handleInput(event) {
  emit('update:modelValue', event.target.value);
}

function handleFocus(event) {
  console.log('[InputText] Focus detected, shouldShowKeyboard:', shouldShowKeyboard.value);

  if (shouldShowKeyboard.value) {
    // Blur the input to prevent native keyboard
    event.target.blur();

    console.log('[InputText] Opening virtual keyboard');
    keyboard.open({
      value: props.modelValue,
      placeholder: props.placeholder,
      onSubmit: (newValue) => {
        console.log('[InputText] Keyboard submitted value:', newValue);
        emit('update:modelValue', newValue);
      }
    });
  }

  emit('focus', event);
}

function handleBlur(event) {
  emit('blur', event);
}

defineExpose({
  inputRef
});
</script>

<style scoped>

input {
  width: 100%;
  outline: none;
}

input:focus {
  border-color: var(--color-brand);
}

input:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>