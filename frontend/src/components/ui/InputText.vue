<!-- frontend/src/components/ui/InputText.vue -->
<template>
  <div class="input-wrapper">
    <div v-press="type !== 'password'" class="input-container" :class="{ 'keyboard-active': isKeyboardActiveForThis }" @click="handleContainerClick">
      <input ref="inputRef" :type="inputType" :value="modelValue" :placeholder="placeholder" :disabled="disabled"
        :maxlength="maxlength" class="heading-4" @input="handleInput" @focus="handleFocus"
        @blur="handleBlur" @keydown.enter="handleSubmit" />
      <!-- A password can always be read back: inside the container, so a tap
           on it keeps the virtual keyboard open, and stopped there, so it
           never opens the keyboard. Chrome moves the focus on mousedown even
           when pointerdown is cancelled, so it is mousedown that keeps the
           focus (and a phone's keyboard) in the field. -->
      <IconButton v-if="isPassword" class="input-reveal" variant="ghost" size="small"
        :icon="revealed ? 'eyeSlash' : 'eye'" :disabled="disabled"
        :aria-label="revealed ? t('common.hidePassword') : t('common.showPassword')"
        @mousedown.prevent @click.stop="revealed = !revealed" />
      <SvgIcon v-else-if="icon" :name="icon" :size="iconSize" class="input-icon" />
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onUnmounted } from 'vue';
import { useI18n } from '@/services/i18n';
import { useVirtualKeyboard, useKeyboardAvailability } from '@/composables/useVirtualKeyboard';
import SvgIcon from '@/components/ui/SvgIcon.vue';
import IconButton from '@/components/ui/IconButton.vue';

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
  // What the value may be completed into, as it is typed (a search's
  // completions): on the virtual keyboard they are drawn above its display,
  // where picking one submits it. null: the field suggests nothing.
  suggestions: {
    type: Array,
    default: null
  }
});

const emit = defineEmits(['update:modelValue', 'blur', 'submit']);

const { t } = useI18n();
const inputRef = ref(null);
const keyboard = useVirtualKeyboard();
const { shouldShowKeyboard } = useKeyboardAvailability();

// Track if the virtual keyboard is active for THIS specific input
const isKeyboardActiveForThis = ref(false);

// A password starts masked, and its eye shows it in clear. The ref itself is
// handed to the virtual keyboard, whose display and eye follow and write it.
const isPassword = computed(() => props.type === 'password');
const revealed = ref(false);
const inputType = computed(() => (isPassword.value && revealed.value ? 'text' : props.type));

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

// A tap on the field is a focus then a click, and each opens the keyboard:
// the second would close it for this very field first (keyboard.open notifies
// the field it held it), leaving the field believing it no longer holds it —
// no active outline, no suggestions. The keyboard already this field's stays.
function openKeyboard() {
  if (isKeyboardActiveForThis.value && keyboard.isVisible.value) return;
  keyboard.open({
    value: props.modelValue,
    placeholder: props.placeholder,
    maxlength: props.maxlength,
    secret: isPassword.value,
    revealed,
    suggestions: props.suggestions,
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
  // After open(): handing the keyboard over notifies the field that held it.
  isKeyboardActiveForThis.value = true;
}

// The suggestions follow the value: the open keyboard draws the latest.
watch(() => props.suggestions, (list) => {
  if (isKeyboardActiveForThis.value) keyboard.setSuggestions(list);
});

function handleFocus(event) {
  if (shouldShowKeyboard.value) {
    // Blur the input to prevent native keyboard
    event.target.blur();
    openKeyboard();
  }
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
  height: 48px;
  padding: 0 var(--space-03) 0 var(--space-04);
  border-radius: var(--radius-04);
  /* An inset well, edged by a hairline that takes no layout space. */
  background: var(--color-inset);
  box-shadow: inset 0 0 0 1px var(--color-border);
  gap: var(--space-03);
  transition: background-color var(--transition-fast), box-shadow var(--transition-fast), opacity var(--transition-fast), var(--transition-press);
}

.input-container:has(input:disabled) {
  opacity: var(--opacity-disabled);
  cursor: not-allowed;
}

/* Focused (or written to by the virtual keyboard): the field lifts onto the
   panel, ringed by one brand pixel. */
.input-container:focus-within,
.input-container.keyboard-active {
  background: var(--color-surface);
  box-shadow: inset 0 0 0 1px var(--color-brand);
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

.input-reveal {
  flex-shrink: 0;
  margin-right: calc(-1 * var(--space-02));
  color: var(--color-text-secondary);
}

.input-icon {
  flex-shrink: 0;
  color: var(--color-text-secondary);
  pointer-events: none;
}

/* Mobile: a medium Button's height, so a field and the button beside it line up. */
@media (max-aspect-ratio: 4/3) and (max-width: 1023px) {
  .input-container {
    height: 38px;
    border-radius: var(--radius-03);
  }
}
</style>