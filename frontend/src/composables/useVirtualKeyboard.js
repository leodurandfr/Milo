// frontend/src/composables/useVirtualKeyboard.js
import { ref } from 'vue';

// Global state (shared across all components)
const isVisible = ref(false);
const currentValue = ref('');
const placeholder = ref('');
const onSubmitCallback = ref(null);
const onCloseCallback = ref(null);

export function useVirtualKeyboard() {
  /**
   * Open the virtual keyboard
   * @param {Object} options - Configuration options
   * @param {string} options.value - Initial value
   * @param {string} options.placeholder - Placeholder text
   * @param {Function} options.onSubmit - Callback when user submits (receives new value)
   * @param {Function} options.onClose - Callback when keyboard closes (receives current value)
   */
  function open(options = {}) {
    currentValue.value = options.value || '';
    placeholder.value = options.placeholder || '';
    onSubmitCallback.value = options.onSubmit || null;
    onCloseCallback.value = options.onClose || null;
    isVisible.value = true;
  }

  /**
   * Close the virtual keyboard (cancel without saving)
   */
  function close() {
    isVisible.value = false;
    // Reset callbacks
    onSubmitCallback.value = null;
    onCloseCallback.value = null;
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
  }

  /**
   * Update the current value
   */
  function updateValue(newValue) {
    currentValue.value = newValue;
  }

  return {
    // State
    isVisible,
    currentValue,
    placeholder,

    // Methods
    open,
    close,
    submit,
    updateValue
  };
}
