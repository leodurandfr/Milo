// frontend/src/composables/useVirtualKeyboard.js
import { ref, computed } from 'vue';
import { isKiosk } from '@/utils/kiosk';

/**
 * Whether the on-screen virtual keyboard is allowed on this device.
 * The keyboard is exclusive to the Pi's own touchscreen (the kiosk) — same
 * `isKiosk` signal used for the color filter, ui_scale and the automatic
 * full-screen player.
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
const maxLength = ref(null);
const secret = ref(false);
// What the field suggests completing its value into, drawn above it in the
// keyboard while there are some: null for a field that suggests nothing.
const suggestions = ref(null);

// Counts code points, as the backend's max_length does, so a surrogate pair is
// never split. Like a native maxlength, a value already past the bound when the
// field opened can shrink but not grow.
function bounded(value) {
  if (maxLength.value === null) return value;
  const limit = Math.max(maxLength.value, Array.from(currentValue.value).length);
  const chars = Array.from(value);
  return chars.length > limit ? chars.slice(0, limit).join('') : value;
}

export function useVirtualKeyboard() {
  /**
   * Open the virtual keyboard
   * @param {Object} options - Configuration options
   * @param {string} options.value - Initial value
   * @param {string} options.placeholder - Placeholder text
   * @param {Function} options.onSubmit - Callback when user submits (receives new value)
   * @param {Function} options.onClose - Callback when keyboard closes (receives current value)
   * @param {Function} options.onChange - Callback when value changes in real-time (receives new value)
   * @param {number} [options.maxlength] - Longest value the keys may type; the
   *   field's own `maxlength` bounds nothing here, since the keys never type
   *   into its `<input>`
   * @param {boolean} [options.secret] - Mask what the keys type, as the field's
   *   own `type="password"` masks nothing on the keyboard's display
   * @param {string[]|null} [options.suggestions] - What the field suggests
   *   completing its value into; null for none (see setSuggestions)
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
    maxLength.value = options.maxlength ?? null;
    secret.value = !!options.secret;
    suggestions.value = options.suggestions ?? null;
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
    maxLength.value = null;
    secret.value = false;
    suggestions.value = null;
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
    maxLength.value = null;
    secret.value = false;
    suggestions.value = null;
  }

  /**
   * Replace the suggestions of the field the keyboard is open for (they follow
   * its value as it is typed). Ignored while it is closed.
   */
  function setSuggestions(list) {
    if (isVisible.value) suggestions.value = list ?? null;
  }

  /**
   * A suggestion is the value: it replaces what was typed and is submitted,
   * as the enter key does.
   */
  function pickSuggestion(text) {
    updateValue(text);
    submit();
  }

  /**
   * Update the current value and trigger onChange callback
   */
  function updateValue(newValue) {
    const value = bounded(newValue);
    currentValue.value = value;
    if (onChangeCallback.value) {
      onChangeCallback.value(value);
    }
  }

  return {
    // State
    isVisible,
    currentValue,
    placeholder,
    originElement,
    secret,
    suggestions,

    // Methods
    open,
    close,
    submit,
    updateValue,
    setSuggestions,
    pickSuggestion
  };
}
