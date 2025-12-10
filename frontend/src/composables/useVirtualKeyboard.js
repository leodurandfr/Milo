// frontend/src/composables/useVirtualKeyboard.js
import { ref, watch } from 'vue';

// Global state (shared across all components)
const isVisible = ref(false);
const currentValue = ref('');
const initialValue = ref('');
const placeholder = ref('');
const onSubmitCallback = ref(null);
const onCloseCallback = ref(null);
const onChangeCallback = ref(null);

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
    initialValue.value = options.value || '';
    placeholder.value = options.placeholder || '';
    onSubmitCallback.value = options.onSubmit || null;
    onCloseCallback.value = options.onClose || null;
    onChangeCallback.value = options.onChange || null;
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

  /**
   * Cancel changes and restore initial value
   */
  function cancel() {
    currentValue.value = initialValue.value;
    if (onChangeCallback.value) {
      onChangeCallback.value(initialValue.value);
    }
  }

  return {
    // State
    isVisible,
    currentValue,
    initialValue,
    placeholder,

    // Methods
    open,
    close,
    submit,
    updateValue,
    cancel
  };
}
