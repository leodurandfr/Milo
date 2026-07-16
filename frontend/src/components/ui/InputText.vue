<!-- frontend/src/components/ui/InputText.vue -->
<template>
  <div class="input-wrapper">
    <div v-press="type !== 'password'" class="input-container" :class="[`input-container--${variant}`, { 'keyboard-active': isKeyboardActiveForThis }]" @click="handleContainerClick">
      <input ref="inputRef" :type="type" :value="modelValue" :placeholder="placeholder" :disabled="disabled"
        :maxlength="maxlength" class="heading-3" @input="handleInput" @focus="handleFocus"
        @blur="handleBlur" @keydown.enter="handleSubmit" />
      <SvgIcon v-if="icon" :name="icon" :size="iconSize" class="input-icon" />
    </div>
  </div>
</template>

<script setup>
import { ref, onUnmounted } from 'vue';
import { useVirtualKeyboard, useKeyboardAvailability } from '@/composables/useVirtualKeyboard';
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
  },
  variant: {
    type: String,
    default: 'outline',
    validator: (value) => ['outline', 'background-neutral'].includes(value)
  }
});

const emit = defineEmits(['update:modelValue', 'focus', 'blur', 'submit']);

const inputRef = ref(null);
const keyboard = useVirtualKeyboard();
const { shouldShowKeyboard } = useKeyboardAvailability();

// Track if the virtual keyboard is active for THIS specific input
const isKeyboardActiveForThis = ref(false);

function handleInput(event) {
  emit('update:modelValue', event.target.value);
}

function handleContainerClick() {
  if (props.disabled) return;

  if (shouldShowKeyboard.value) {
    // Open virtual keyboard (handles both new activation and switching from another field)
    openKeyboard();
  } else {
    inputRef.value?.focus();
  }
}

function openKeyboard() {
  isKeyboardActiveForThis.value = true;
  keyboard.open({
    value: props.modelValue,
    placeholder: props.placeholder,
    originElement: inputRef.value,
    onChange: (newValue) => {
      emit('update:modelValue', newValue);
    },
    onSubmit: (newValue) => {
      emit('update:modelValue', newValue);
      emit('submit', newValue);
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

function handleSubmit() {
  emit('submit', props.modelValue);
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

/* Background-neutral variant */
.input-container--background-neutral {
  box-shadow: none;
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