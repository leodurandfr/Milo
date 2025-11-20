<!-- frontend/src/components/ui/VirtualKeyboard.vue -->
<template>
  <!-- Component always mounted for computed to work -->
  <div>
    <Transition name="keyboard">
      <div v-if="isKeyboardVisible && shouldShowKeyboard" class="virtual-keyboard-overlay" @click.self="handleClose">
      <div class="virtual-keyboard">
        <div class="keyboard-header">
          <div class="keyboard-input-display">
            <input
              ref="displayInput"
              type="text"
              v-model="keyboardValue"
              :placeholder="keyboardPlaceholder"
              class="keyboard-display-input text-body"
              readonly
            />
          </div>
          <button class="keyboard-close-btn" @click="handleClose">
            <SvgIcon name="close" :size="24" />
          </button>
        </div>

        <div class="keyboard-keys">
          <!-- Row 1 -->
          <div class="keyboard-row">
            <button
              v-for="key in isUppercase ? row1Upper : row1"
              :key="key"
              class="keyboard-key"
              @click="addChar(key)"
            >
              {{ key }}
            </button>
            <button class="keyboard-key key-backspace" @click="backspace">
              ⌫
            </button>
          </div>

          <!-- Row 2 -->
          <div class="keyboard-row">
            <button
              v-for="key in isUppercase ? row2Upper : row2"
              :key="key"
              class="keyboard-key"
              @click="addChar(key)"
            >
              {{ key }}
            </button>
          </div>

          <!-- Row 3 -->
          <div class="keyboard-row">
            <button class="keyboard-key key-shift" @click="toggleCase">
              ⇧
            </button>
            <button
              v-for="key in isUppercase ? row3Upper : row3"
              :key="key"
              class="keyboard-key"
              @click="addChar(key)"
            >
              {{ key }}
            </button>
          </div>

          <!-- Row 4 -->
          <div class="keyboard-row">
            <button
              v-for="key in isUppercase ? row4Upper : row4"
              :key="key"
              class="keyboard-key"
              @click="addChar(key)"
            >
              {{ key }}
            </button>
            <button class="keyboard-key key-backspace-bottom" @click="backspace">
              ⌫
            </button>
          </div>

          <!-- Row 5 - Space and Enter -->
          <div class="keyboard-row">
            <button class="keyboard-key key-space" @click="addChar(' ')">
              Espace
            </button>
            <button class="keyboard-key key-enter" @click="handleSubmit">
              ✓
            </button>
          </div>
        </div>
      </div>
      </div>
    </Transition>
  </div>
</template>

<script setup>
import { ref, watch, computed, onMounted, onUnmounted } from 'vue';
import { useI18n } from '@/services/i18n';
import { useVirtualKeyboard } from '@/composables/useVirtualKeyboard';
import { useHardwareConfig } from '@/composables/useHardwareConfig';
import SvgIcon from './SvgIcon.vue';

const { getCurrentLanguage } = useI18n();
const keyboardState = useVirtualKeyboard();
const { screenResolution } = useHardwareConfig();

// Create local computed refs for reactivity in template
const isKeyboardVisible = computed(() => keyboardState.isVisible.value);
const keyboardValue = computed({
  get: () => keyboardState.currentValue.value,
  set: (val) => keyboardState.updateValue(val)
});
const keyboardPlaceholder = computed(() => keyboardState.placeholder.value);

const isUppercase = ref(false);

// Detect if keyboard should be shown based on screen resolution from milo_hardware.json
// The keyboard appears only when browser resolution matches the configured screen resolution
const shouldShowKeyboard = computed(() => {
  // Force mode via URL parameter (for development/testing)
  const urlParams = new URLSearchParams(window.location.search);
  if (urlParams.get('virtualKeyboard') === 'true') {
    console.log('[VirtualKeyboard] Force mode enabled via URL');
    return true;
  }

  // Get configured resolution from milo_hardware.json
  // Use toRaw or direct property access to handle Vue Proxy correctly
  const configuredResolution = screenResolution.value;
  const currentWidth = window.innerWidth;
  const currentHeight = window.innerHeight;

  // Extract width/height from potential Proxy object
  const configWidth = configuredResolution?.width;
  const configHeight = configuredResolution?.height;

  console.log('[VirtualKeyboard] shouldShowKeyboard check:', {
    configWidth,
    configHeight,
    browserWidth: currentWidth,
    browserHeight: currentHeight,
    rawConfigured: JSON.stringify(configuredResolution)
  });

  // If no valid resolution configured, don't show keyboard
  if (!configWidth || !configHeight) {
    console.log('[VirtualKeyboard] ❌ No valid resolution configured');
    return false;
  }

  // Show keyboard ONLY if resolutions match exactly
  const matches = currentWidth === configWidth && currentHeight === configHeight;

  console.log(`[VirtualKeyboard] Resolution ${matches ? '✅ MATCH' : '❌ NO MATCH'} (${currentWidth}x${currentHeight} vs ${configWidth}x${configHeight})`);

  return matches;
});

// Keyboard layouts
const keyboardLayouts = {
  // AZERTY French layout
  french: {
    row1: ['1', '2', '3', '4', '5', '6', '7', '8', '9', '0'],
    row1Upper: ['&', 'é', '"', "'", '(', '-', 'è', '_', 'ç', 'à'],
    row2: ['a', 'z', 'e', 'r', 't', 'y', 'u', 'i', 'o', 'p'],
    row2Upper: ['A', 'Z', 'E', 'R', 'T', 'Y', 'U', 'I', 'O', 'P'],
    row3: ['q', 's', 'd', 'f', 'g', 'h', 'j', 'k', 'l', 'm'],
    row3Upper: ['Q', 'S', 'D', 'F', 'G', 'H', 'J', 'K', 'L', 'M'],
    row4: ['w', 'x', 'c', 'v', 'b', 'n', ',', '.', '!', '?'],
    row4Upper: ['W', 'X', 'C', 'V', 'B', 'N', ';', ':', '/', '!']
  },

  // QWERTY Spanish layout
  spanish: {
    row1: ['1', '2', '3', '4', '5', '6', '7', '8', '9', '0'],
    row1Upper: ['¡', '¿', '#', '$', '%', '&', '/', '(', ')', '='],
    row2: ['q', 'w', 'e', 'r', 't', 'y', 'u', 'i', 'o', 'p'],
    row2Upper: ['Q', 'W', 'E', 'R', 'T', 'Y', 'U', 'I', 'O', 'P'],
    row3: ['a', 's', 'd', 'f', 'g', 'h', 'j', 'k', 'l', 'ñ'],
    row3Upper: ['A', 'S', 'D', 'F', 'G', 'H', 'J', 'K', 'L', 'Ñ'],
    row4: ['z', 'x', 'c', 'v', 'b', 'n', 'm', 'á', 'é', 'ó'],
    row4Upper: ['Z', 'X', 'C', 'V', 'B', 'N', 'M', 'Á', 'É', 'Ó']
  },

  // QWERTY US/International layout (default for other languages)
  default: {
    row1: ['1', '2', '3', '4', '5', '6', '7', '8', '9', '0'],
    row1Upper: ['!', '@', '#', '$', '%', '^', '&', '*', '(', ')'],
    row2: ['q', 'w', 'e', 'r', 't', 'y', 'u', 'i', 'o', 'p'],
    row2Upper: ['Q', 'W', 'E', 'R', 'T', 'Y', 'U', 'I', 'O', 'P'],
    row3: ['a', 's', 'd', 'f', 'g', 'h', 'j', 'k', 'l', ';'],
    row3Upper: ['A', 'S', 'D', 'F', 'G', 'H', 'J', 'K', 'L', ':'],
    row4: ['z', 'x', 'c', 'v', 'b', 'n', 'm', ',', '.', '?'],
    row4Upper: ['Z', 'X', 'C', 'V', 'B', 'N', 'M', '<', '>', '!']
  }
};

// Get current layout based on language
const currentLayout = computed(() => {
  const lang = getCurrentLanguage();

  if (lang === 'french') {
    return keyboardLayouts.french;
  } else if (lang === 'spanish') {
    return keyboardLayouts.spanish;
  } else {
    // english, german, italian, portuguese, hindi, chinese
    return keyboardLayouts.default;
  }
});

// Dynamic rows based on current layout
const row1 = computed(() => currentLayout.value.row1);
const row1Upper = computed(() => currentLayout.value.row1Upper);
const row2 = computed(() => currentLayout.value.row2);
const row2Upper = computed(() => currentLayout.value.row2Upper);
const row3 = computed(() => currentLayout.value.row3);
const row3Upper = computed(() => currentLayout.value.row3Upper);
const row4 = computed(() => currentLayout.value.row4);
const row4Upper = computed(() => currentLayout.value.row4Upper);

function addChar(char) {
  keyboardValue.value += char;
}

function backspace() {
  keyboardValue.value = keyboardValue.value.slice(0, -1);
}

function toggleCase() {
  isUppercase.value = !isUppercase.value;
}

function handleClose() {
  keyboardState.close();
}

function handleSubmit() {
  keyboardState.submit();
}
</script>

<style scoped>
.virtual-keyboard-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.7);
  backdrop-filter: blur(8px);
  z-index: 6000;
  display: flex;
  align-items: flex-end;
  justify-content: center;
  padding: var(--space-04);
}

.virtual-keyboard {
  width: 100%;
  max-width: 800px;
  background: var(--color-background-neutral);
  border-radius: var(--radius-05);
  padding: var(--space-04);
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
  border: 2px solid var(--color-background-medium-16);
  border-radius: var(--radius-04);
  background: var(--color-background-strong);
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

.key-backspace,
.key-backspace-bottom,
.key-shift {
  flex: 1.5;
  font-size: var(--font-size-large);
}

.key-space {
  flex: 6;
}

.key-enter {
  flex: 2;
  background: var(--color-brand);
  color: white;
  font-size: var(--font-size-large);
}

.key-enter:active {
  transform: scale(0.95);
}

/* Transitions */
.keyboard-enter-active,
.keyboard-leave-active {
  transition: opacity 300ms ease;
}

.keyboard-enter-active .virtual-keyboard,
.keyboard-leave-active .virtual-keyboard {
  transition: transform 300ms ease;
}

.keyboard-enter-from,
.keyboard-leave-to {
  opacity: 0;
}

.keyboard-enter-from .virtual-keyboard {
  transform: translateY(100%);
}

.keyboard-leave-to .virtual-keyboard {
  transform: translateY(100%);
}

/* Mobile adjustments */
@media (max-aspect-ratio: 4/3) {
  .virtual-keyboard-overlay {
    padding: 0;
  }

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
