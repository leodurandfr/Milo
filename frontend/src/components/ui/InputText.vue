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

// Détection si la résolution correspond (pour afficher le clavier virtuel)
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

  // Si aucune résolution valide configurée, pas de clavier virtuel
  if (!configWidth || !configHeight) {
    console.log('[InputText] ❌ No valid resolution configured');
    return false;
  }

  // Vérifier si les résolutions correspondent exactement
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

// Expose inputRef for parent components if needed
defineExpose({
  inputRef
});
</script>

<style scoped>
/* Les styles de base sont hérités via inputClass prop */
/* Ajoutez ici uniquement des styles spécifiques si nécessaire */
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
