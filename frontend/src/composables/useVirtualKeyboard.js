// frontend/src/composables/useVirtualKeyboard.js
import { ref, computed } from 'vue';
import { isKiosk } from '@/utils/kiosk';

/**
 * Whether the on-screen virtual keyboard is allowed on this device.
 * The keyboard is exclusive to the Pi's own touchscreen (the kiosk) — same
 * `isKiosk` signal used for the color filter, ui_scale and screensaver.
 * `?virtualKeyboard=true` forces it on for dev/testing; the dev server
 * (localhost:5173) is otherwise excluded even though it is technically kiosk.
 */
export function useKeyboardAvailability() {
  const shouldShowKeyboard = computed(() => {
    const urlParams = new URLSearchParams(window.location.search);
    if (urlParams.get('virtualKeyboard') === 'true') return true;
    if (import.meta.env.DEV) return false;
    return isKiosk();
  });
  return { shouldShowKeyboard };
}

// Global state (shared across all components)
const isVisible = ref(false);
const currentValue = ref('');
const placeholder = ref('');
const onSubmitCallback = ref(null);
const onCloseCallback = ref(null);
const onChangeCallback = ref(null);
const originElement = ref(null);

export function useVirtualKeyboard() {
  /**
   * Open the virtual keyboard
   * @param {Object} options - Configuration options
   * @param {string} options.value - Initial value
   * @param {string} options.placeholder - Placeholder text
   * @param {Function} options.onSubmit - Callback when user submits (receives new value)
   * @param {Function} options.onClose - Callback when keyboard closes (receives current value)
   * @param {Function} options.onChange - Callback when value changes in real-time (receives new value)
   */
  function open(options = {}) {
    // If keyboard is already open for another field, notify the previous field
    if (isVisible.value && onCloseCallback.value) {
      onCloseCallback.value(currentValue.value);
    }

    currentValue.value = options.value || '';
    placeholder.value = options.placeholder || '';
    onSubmitCallback.value = options.onSubmit || null;
    onCloseCallback.value = options.onClose || null;
    onChangeCallback.value = options.onChange || null;
    originElement.value = options.originElement || null;
    isVisible.value = true;
  }

  /**
   * Close the virtual keyboard (cancel without saving)
   */
  function close() {
    if (onCloseCallback.value) {
      onCloseCallback.value(currentValue.value);
    }
    isVisible.value = false;
    // Reset callbacks
    onSubmitCallback.value = null;
    onCloseCallback.value = null;
    onChangeCallback.value = null;
    originElement.value = null;
  }

  /**
   * Submit the current value and close
   */
  function submit() {
    if (onSubmitCallback.value) {
      onSubmitCallback.value(currentValue.value);
    }
    isVisible.value = false;
    // Reset callbacks
    onSubmitCallback.value = null;
    onCloseCallback.value = null;
    onChangeCallback.value = null;
    originElement.value = null;
  }

  /**
   * Update the current value and trigger onChange callback
   */
  function updateValue(newValue) {
    currentValue.value = newValue;
    if (onChangeCallback.value) {
      onChangeCallback.value(newValue);
    }
  }

  return {
    // State
    isVisible,
    currentValue,
    placeholder,
    originElement,

    // Methods
    open,
    close,
    submit,
    updateValue
  };
}
