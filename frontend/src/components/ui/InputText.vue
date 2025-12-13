<!-- frontend/src/components/ui/InputText.vue -->
<template>
  <div class="input-wrapper">
    <div v-press.light class="input-container" :class="{ 'keyboard-active': isKeyboardActiveForThis }" @click="handleContainerClick">
      <input ref="inputRef" :type="type" :value="modelValue" :placeholder="placeholder" :disabled="disabled"
        :maxlength="maxlength" class="heading-3" @input="handleInput" @focus="handleFocus"
        @blur="handleBlur" />
      <SvgIcon v-if="icon" :name="icon" :size="iconSize" class="input-icon" />
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onUnmounted } from 'vue';
import { useVirtualKeyboard } from '@/composables/useVirtualKeyboard';
import { useHardwareConfig } from '@/composables/useHardwareConfig';
import SvgIcon from '@/components/ui/SvgIcon.vue';

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
  icon: {
    type: String,
    default: ''
  },
  iconSize: {
    type: Number,
    default: 24
  }
});

const emit = defineEmits(['update:modelValue', 'focus', 'blur']);

const inputRef = ref(null);
const keyboard = useVirtualKeyboard();
const { screenResolution } = useHardwareConfig();

// Track if the virtual keyboard is active for THIS specific input
const isKeyboardActiveForThis = ref(false);

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

function handleContainerClick() {
  if (props.disabled) return;

  if (shouldShowKeyboard.value) {
    // Open virtual keyboard (handles both new activation and switching from another field)
    openKeyboard();
  } else {
    // Focus the native input
    inputRef.value?.focus();
  }
}

function openKeyboard() {
  isKeyboardActiveForThis.value = true;
  keyboard.open({
    value: props.modelValue,
    placeholder: props.placeholder,
    onChange: (newValue) => {
      emit('update:modelValue', newValue);
    },
    onSubmit: (newValue) => {
      emit('update:modelValue', newValue);
      emit('blur');
      isKeyboardActiveForThis.value = false;
    },
    onClose: () => {
      emit('blur');
      isKeyboardActiveForThis.value = false;
    }
  });
}

function handleFocus(event) {
  if (shouldShowKeyboard.value) {
    // Blur the input to prevent native keyboard
    event.target.blur();
    openKeyboard();
  }

  emit('focus', event);
}

function handleBlur(event) {
  emit('blur', event);
}

// Cleanup on unmount - close keyboard if it was opened by this input
onUnmounted(() => {
  if (isKeyboardActiveForThis.value) {
    keyboard.close();
  }
});

defineExpose({
  inputRef
});
</script>

<style scoped>
.input-wrapper {
  position: relative;
  display: flex;
  width: 100%;
  flex: 1;
}

.input-container {
  display: flex;
  align-items: center;
  width: 100%;
  padding: var(--space-03) var(--space-04);
  border-radius: var(--radius-04);
  background: var(--color-background-neutral);
  gap: var(--space-01);
  -webkit-box-shadow: inset 0px 0px 0px 2px var(--color-border);
  -moz-box-shadow: inset 0px 0px 0px 2px var(--color-border);
  box-shadow: inset 0px 0px 0px 2px var(--color-border);
  transition: box-shadow var(--transition-fast), var(--transition-press);
}

.input-container:has(input:disabled) {
  opacity: 0.5;
  cursor: not-allowed;
}

.input-container:focus-within,
.input-container.keyboard-active {
  -webkit-box-shadow: inset 0px 0px 0px 2px var(--color-brand);
  -moz-box-shadow: inset 0px 0px 0px 2px var(--color-brand);
  box-shadow: inset 0px 0px 0px 2px var(--color-brand);
}

input {
  width: 100%;
  flex: 1;
  padding: 0;
  border: none;
  border-radius: 0;
  color: var(--color-text);
  background: transparent;
  outline: none;
  box-shadow: none;
  min-width: 0;
}

input::placeholder {
  color: var(--color-text-secondary);
}

input:disabled {
  cursor: not-allowed;
}

.input-icon {
  flex-shrink: 0;
  color: var(--color-text-secondary);
  pointer-events: none;
}
</style>