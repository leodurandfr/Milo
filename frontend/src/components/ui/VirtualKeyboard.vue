<!-- frontend/src/components/ui/VirtualKeyboard.vue -->
<template>
  <!-- Component always mounted for computed to work -->
  <div>
    <Transition name="keyboard">
      <div v-if="isKeyboardVisible && shouldShowKeyboard" ref="keyboardRef" class="virtual-keyboard">
        <!-- Header: Input display + Cancel + Close buttons -->
        <div class="keyboard-header">
          <div class="keyboard-input-display">
            <input ref="displayInput" type="text" v-model="keyboardValue" :placeholder="keyboardPlaceholder"
              class="keyboard-display-input heading-3" readonly />
          </div>
          <button class="keyboard-cancel-btn" :class="{ 'disabled': !hasChanges }" :disabled="!hasChanges"
            @click="handleCancel">
            <SvgIcon name="reset" :size="24" />
          </button>
          <button class="keyboard-close-btn" @click="handleClose">
            <SvgIcon name="close" :size="24" />
          </button>
        </div>

        <div class="keyboard-keys">
          <!-- Row 1: [tab] + 10 keys + [backspace] -->
          <div class="keyboard-row">
            <button class="keyboard-key key-tab text-mono" @click="addChar('\t')">
              ⇥
            </button>
            <button v-for="key in currentRow1" :key="'r1-' + key" class="keyboard-key text-mono" @click="addChar(key)">
              {{ key }}
            </button>
            <button class="keyboard-key key-backspace text-mono" @click="backspace">
              ⌫
            </button>
          </div>

          <!-- Row 2: [caps] + 9-10 keys + [enter] -->
          <div class="keyboard-row">
            <button class="keyboard-key key-caps text-mono" :class="{ 'caps-active': isCapsLock }"
              @click="toggleCapsLock">
              ⇪
              <span v-if="isCapsLock" class="caps-indicator"></span>
            </button>
            <button v-for="key in currentRow2" :key="'r2-' + key" class="keyboard-key text-mono" @click="addChar(key)">
              {{ key }}
            </button>
            <button class="keyboard-key key-enter text-mono" @click="addChar('\n')">
              ↵
            </button>
          </div>

          <!-- Row 3: [shift] + 9 keys + [shift] -->
          <div class="keyboard-row">
            <button class="keyboard-key key-shift text-mono" :class="{ 'shift-active': isShiftHeld }"
              @click="toggleShift">
              ⇧
            </button>
            <button v-for="key in currentRow3" :key="'r3-' + key" class="keyboard-key text-mono" @click="addChar(key)">
              {{ key }}
            </button>
            <button class="keyboard-key key-shift text-mono" :class="{ 'shift-active': isShiftHeld }"
              @click="toggleShift">
              ⇧
            </button>
          </div>

          <!-- Row 4: [mode-toggle] + [space] + [submit] -->
          <div class="keyboard-row">
            <button class="keyboard-key key-mode text-mono" @click="toggleMode">
              {{ modeToggleLabel }}
            </button>
            <button class="keyboard-key key-space" @click="addChar(' ')">
            </button>
            <button class="keyboard-key key-submit text-mono" @click="handleSubmit">
              ✓
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue';
import { useI18n } from '@/services/i18n';
import { useVirtualKeyboard } from '@/composables/useVirtualKeyboard';
import { useHardwareConfig } from '@/composables/useHardwareConfig';
import SvgIcon from './SvgIcon.vue';

const { getCurrentLanguage } = useI18n();
const keyboardState = useVirtualKeyboard();
const { screenResolution } = useHardwareConfig();

// Ref for the keyboard element to detect outside clicks
const keyboardRef = ref(null);

// Create local computed refs for reactivity in template
const isKeyboardVisible = computed(() => keyboardState.isVisible.value);
const keyboardValue = computed({
  get: () => keyboardState.currentValue.value,
  set: (val) => keyboardState.updateValue(val)
});
const keyboardPlaceholder = computed(() => keyboardState.placeholder.value);
const keyboardInitialValue = computed(() => keyboardState.initialValue.value);

// Check if the value has been modified
const hasChanges = computed(() => keyboardValue.value !== keyboardInitialValue.value);

// Keyboard state
const keyboardMode = ref('abc'); // 'abc', 'numbers', 'symbols'
const isCapsLock = ref(false);
const isShiftHeld = ref(false);

// Computed: is uppercase active (caps lock OR shift held)
const isUppercase = computed(() => isCapsLock.value || isShiftHeld.value);

// Detect if keyboard should be shown based on screen resolution
const shouldShowKeyboard = computed(() => {
  const urlParams = new URLSearchParams(window.location.search);
  if (urlParams.get('virtualKeyboard') === 'true') {
    return true;
  }

  const configuredResolution = screenResolution.value;
  const currentWidth = window.innerWidth;
  const currentHeight = window.innerHeight;
  const configWidth = configuredResolution?.width;
  const configHeight = configuredResolution?.height;

  if (!configWidth || !configHeight) {
    return false;
  }

  return currentWidth === configWidth && currentHeight === configHeight;
});

// Keyboard layouts - iPad style (3 languages x 3 modes)
const keyboardLayouts = {
  // AZERTY French layout
  french: {
    abc: {
      row1: ['a', 'z', 'e', 'r', 't', 'y', 'u', 'i', 'o', 'p'],
      row2: ['q', 's', 'd', 'f', 'g', 'h', 'j', 'k', 'l', 'm'],
      row3: ['w', 'x', 'c', 'v', 'b', 'n', "'", '?', '!'],
    },
    numbers: {
      row1: ['1', '2', '3', '4', '5', '6', '7', '8', '9', '0'],
      row2: ['@', '#', '&', '"', '€', '(', '!', ')', '-', '*'],
      row3: ['%', '_', '+', '=', '/', ':', ';', "'", '?'],
    },
    symbols: {
      row1: ['1', '2', '3', '4', '5', '6', '7', '8', '9', '0'],
      row2: ['~', '°', '[', ']', '{', '}', '^', '$', '£', '¥'],
      row3: ['§', '<', '>', '|', '\\', '…', '.', "'", '?'],
    }
  },

  // QWERTY English layout (default)
  english: {
    abc: {
      row1: ['q', 'w', 'e', 'r', 't', 'y', 'u', 'i', 'o', 'p'],
      row2: ['a', 's', 'd', 'f', 'g', 'h', 'j', 'k', 'l'],
      row3: ['z', 'x', 'c', 'v', 'b', 'n', 'm', ',', '!', '?'],
    },
    numbers: {
      row1: ['1', '2', '3', '4', '5', '6', '7', '8', '9', '0'],
      row2: ['@', '#', '€', '&', '*', '(', ')', "'", '"'],
      row3: ['%', '-', '+', '=', '/', ';', ':', '!', '?'],
    },
    symbols: {
      row1: ['1', '2', '3', '4', '5', '6', '7', '8', '9', '0'],
      row2: ['$', '£', '¥', '_', '^', '[', ']', '{', '}'],
      row3: ['§', '|', '~', '…', '\\', '<', '>', '!', '?'],
    }
  },

  // QWERTY Spanish layout
  spanish: {
    abc: {
      row1: ['q', 'w', 'e', 'r', 't', 'y', 'u', 'i', 'o', 'p'],
      row2: ['a', 's', 'd', 'f', 'g', 'h', 'j', 'k', 'l', 'ñ'],
      row3: ['z', 'x', 'c', 'v', 'b', 'n', 'm', ',', '!', '?'],
    },
    numbers: {
      row1: ['1', '2', '3', '4', '5', '6', '7', '8', '9', '0'],
      row2: ['@', '#', '€', '&', '*', '(', ')', '¿', '¡'],
      row3: ['%', '-', '+', '=', '/', ';', ':', '!', '?'],
    },
    symbols: {
      row1: ['1', '2', '3', '4', '5', '6', '7', '8', '9', '0'],
      row2: ['$', '£', '¥', '_', '^', '[', ']', '{', '}'],
      row3: ['§', '|', '~', '…', '\\', '<', '>', 'á', 'é'],
    }
  }
};

// Get current layout based on language
const currentLayoutData = computed(() => {
  const lang = getCurrentLanguage();

  if (lang === 'french') {
    return keyboardLayouts.french;
  } else if (lang === 'spanish') {
    return keyboardLayouts.spanish;
  } else {
    // english, german, italian, portuguese, hindi, chinese - use english QWERTY
    return keyboardLayouts.english;
  }
});

// Get current mode layout
const currentModeLayout = computed(() => {
  return currentLayoutData.value[keyboardMode.value];
});

// Transform keys based on uppercase state (only for abc mode)
const transformKey = (key) => {
  if (keyboardMode.value === 'abc' && isUppercase.value) {
    return key.toUpperCase();
  }
  return key;
};

// Current rows with uppercase transformation
const currentRow1 = computed(() => currentModeLayout.value.row1.map(transformKey));
const currentRow2 = computed(() => currentModeLayout.value.row2.map(transformKey));
const currentRow3 = computed(() => currentModeLayout.value.row3.map(transformKey));

// Mode toggle label
const modeToggleLabel = computed(() => {
  if (keyboardMode.value === 'abc') {
    return '.?123';
  } else if (keyboardMode.value === 'numbers') {
    return '#+=';
  } else {
    return 'ABC';
  }
});

// Functions
function addChar(char) {
  keyboardValue.value += char;
  // Reset shift after typing (but not caps lock)
  if (isShiftHeld.value) {
    isShiftHeld.value = false;
  }
}

function backspace() {
  keyboardValue.value = keyboardValue.value.slice(0, -1);
}

function toggleCapsLock() {
  isCapsLock.value = !isCapsLock.value;
}

function toggleShift() {
  isShiftHeld.value = !isShiftHeld.value;
}

function toggleMode() {
  if (keyboardMode.value === 'abc') {
    keyboardMode.value = 'numbers';
  } else if (keyboardMode.value === 'numbers') {
    keyboardMode.value = 'symbols';
  } else {
    keyboardMode.value = 'abc';
  }
}

function handleCancel() {
  keyboardState.cancel();
}

function handleClose() {
  keyboardState.close();
  // Reset state when closing
  keyboardMode.value = 'abc';
  isCapsLock.value = false;
  isShiftHeld.value = false;
}

function handleSubmit() {
  keyboardState.submit();
  // Reset state when submitting
  keyboardMode.value = 'abc';
  isCapsLock.value = false;
  isShiftHeld.value = false;
}

// Handle clicks outside the keyboard and InputText fields
function handleOutsideClick(event) {
  // Check if click is inside the keyboard
  if (keyboardRef.value && keyboardRef.value.contains(event.target)) {
    return;
  }

  // Check if click is on an InputText (has input-container class or is inside one)
  const inputContainer = event.target.closest('.input-container');
  if (inputContainer) {
    return;
  }

  // Click is outside both keyboard and InputText - close the keyboard
  handleClose();
}

// Add/remove global click listener when keyboard visibility changes
watch(isKeyboardVisible, (visible) => {
  if (visible) {
    // Use setTimeout to avoid catching the click that opened the keyboard
    setTimeout(() => {
      document.addEventListener('pointerdown', handleOutsideClick);
    }, 0);
  } else {
    document.removeEventListener('pointerdown', handleOutsideClick);
  }
});

// Cleanup on unmount
onUnmounted(() => {
  document.removeEventListener('pointerdown', handleOutsideClick);
});
</script>

<style scoped>
.virtual-keyboard {
  position: fixed;
  bottom: 0;
  left: 50%;
  transform: translateX(-50%);
  z-index: 6000;
  width: 100%;
  max-width: 1024px;
  background: var(--color-background-neutral);
  border-radius: var(--radius-05) var(--radius-05) 0 0;
  padding: var(--space-06);
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
}

.keyboard-header {
  display: flex;
  gap: var(--space-03);
  margin-bottom: var(--space-04);
}

.keyboard-input-display {
  flex: 1;
}

.keyboard-display-input {
  width: 100%;
  padding: var(--space-03) var(--space-04);
  border: 0px;
  box-shadow: inset 0 0 0 1px var(--color-border);
  border-radius: var(--radius-04);
  background: var(--color-background);
  color: var(--color-text);
  font-size: var(--font-size-large);
  text-align: center;
}

.keyboard-close-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 48px;
  height: 48px;
  background: var(--color-background-strong);
  border: none;
  border-radius: var(--radius-04);
  color: var(--color-text);
  cursor: pointer;
  transition: background var(--transition-fast);
}

.keyboard-close-btn:hover {
  background: var(--color-background-medium-16);
}

.keyboard-cancel-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 48px;
  height: 48px;
  background: var(--color-background-strong);
  border: none;
  border-radius: var(--radius-04);
  color: var(--color-text);
  cursor: pointer;
  transition: background var(--transition-fast);
}

.keyboard-cancel-btn:hover:not(:disabled) {
  background: var(--color-background-medium-16);
}

.keyboard-cancel-btn.disabled {
  opacity: 0.3;
  cursor: not-allowed;
}

.keyboard-keys {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.keyboard-row {
  display: flex;
  gap: var(--space-02);
  justify-content: center;
}

.keyboard-key {
  flex: 1;
  min-width: 0;
  height: 48px;
  background: var(--color-background-strong);
  border: none;
  border-radius: var(--radius-03);
  color: var(--color-text);
  font-size: var(--font-size-base);
  font-weight: 500;
  cursor: pointer;
  transition: all var(--transition-fast);
  user-select: none;
  -webkit-tap-highlight-color: transparent;
}

.keyboard-key:active {
  background: var(--color-brand);
  transform: scale(0.95);
}

/* Special keys sizing */
.key-tab,
.key-backspace {
  flex: 1.2;
  font-size: var(--font-size-large);
}

.key-caps,
.key-enter {
  flex: 1.4;
  font-size: var(--font-size-large);
  position: relative;
}

.key-shift {
  flex: 1.5;
  font-size: var(--font-size-large);
}

.key-mode {
  flex: 1.5;
  font-size: var(--font-size-small);
}

.key-space {
  flex: 5;
}

.key-submit {
  flex: 1.5;
  background: var(--color-brand);
  color: white;
  font-size: var(--font-size-large);
}

.key-submit:active {
  transform: scale(0.95);
}

/* Caps Lock indicator */
.key-caps {
  position: relative;
}

.caps-indicator {
  position: absolute;
  top: 6px;
  right: 6px;
  width: 6px;
  height: 6px;
  background: var(--color-success, #22c55e);
  border-radius: 50%;
}

.caps-active {
  background: var(--color-background-medium-16);
}

/* Shift active state */
.shift-active {
  background: var(--color-background-medium-16);
}

/* Transitions */
.keyboard-enter-active,
.keyboard-leave-active {
  transition: transform 300ms ease, opacity 300ms ease;
}

.keyboard-enter-from,
.keyboard-leave-to {
  opacity: 0;
  transform: translateX(-50%) translateY(100%);
}

/* Mobile adjustments */
@media (max-aspect-ratio: 4/3) {
  .virtual-keyboard {
    max-width: 100%;
    border-radius: var(--radius-05) var(--radius-05) 0 0;
  }

  .keyboard-key {
    height: 56px;
    font-size: var(--font-size-large);
  }
}
</style>
