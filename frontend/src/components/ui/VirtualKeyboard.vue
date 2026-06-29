<!-- frontend/src/components/ui/VirtualKeyboard.vue -->
<template>
  <div>
    <Transition name="keyboard">
      <div v-if="isKeyboardVisible && shouldShowKeyboard" ref="keyboardRef" class="virtual-keyboard">

        <!-- Header: Input display + Backspace -->
        <div class="keyboard-header">
          <div class="keyboard-input-display">
            <input ref="displayInput" type="text" v-model="keyboardValue" :placeholder="keyboardPlaceholder"
              class="keyboard-display-input heading-3" inputmode="none" />
          </div>
          <button class="keyboard-key key-backspace"
            @pointerdown.prevent="startBackspaceRepeat"
            @pointerup.prevent="stopBackspaceRepeat"
            @pointerleave="stopBackspaceRepeat">
            <SvgIcon name="keyboardDelete" :size="24" />
          </button>
        </div>

        <div class="keyboard-keys">
          <!-- Row 1: 10 character keys -->
          <div class="keyboard-row">
            <button v-for="key in currentRow1" :key="'r1-' + key"
              class="keyboard-key text-body"
              @pointerdown.prevent="onKeyPointerDown($event, key)"
              @pointerup.prevent="onKeyPointerUp($event, key)"
              @pointerleave="onKeyPointerLeave"
              @pointermove="onKeyPointerMove">
              {{ key }}
            </button>
          </div>

          <!-- Row 2: 10 character keys -->
          <div class="keyboard-row">
            <button v-for="key in currentRow2" :key="'r2-' + key"
              class="keyboard-key text-body"
              @pointerdown.prevent="onKeyPointerDown($event, key)"
              @pointerup.prevent="onKeyPointerUp($event, key)"
              @pointerleave="onKeyPointerLeave"
              @pointermove="onKeyPointerMove">
              {{ key }}
            </button>
          </div>

          <!-- Row 3: [caps/#+=/123] + character keys + [submit →] -->
          <div class="keyboard-row">
            <button class="keyboard-key key-caps"
              :class="{ 'caps-active': isCapsLock && keyboardMode === 'abc' }"
              @pointerdown.prevent
              @click="handleRow3Left">
              <template v-if="keyboardMode === 'abc'">
                <SvgIcon :name="isCapsLock ? 'keyboardCapsLockFilled' : 'keyboardCapsLock'" :size="24" />
              </template>
              <template v-else-if="keyboardMode === 'numbers'"><span class="text-body">#+=</span></template>
              <template v-else><span class="text-body">123</span></template>
            </button>

            <button v-for="key in currentRow3" :key="'r3-' + key"
              class="keyboard-key text-body"
              @pointerdown.prevent="onKeyPointerDown($event, key)"
              @pointerup.prevent="onKeyPointerUp($event, key)"
              @pointerleave="onKeyPointerLeave"
              @pointermove="onKeyPointerMove">
              {{ key }}
            </button>

            <button class="keyboard-key key-enter"
              @pointerdown.prevent
              @click="handleSubmit">
              <SvgIcon name="keyboardEnter" :size="24" />
            </button>
          </div>

          <!-- Row 4: [shift] + [mode toggle] + [space] + [dismiss ⌨] -->
          <div class="keyboard-row">
            <button class="keyboard-key key-shift"
              :class="{ 'shift-active': isShiftHeld }"
              @pointerdown.prevent
              @click="toggleShift">
              <SvgIcon name="keyboardShift" :size="24" />
            </button>
            <button class="keyboard-key key-mode text-body"
              :class="{ 'mode-active': keyboardMode !== 'abc' }"
              @pointerdown.prevent
              @click="toggleMode">
              {{ modeToggleLabel }}
            </button>
            <button class="keyboard-key key-space"
              @pointerdown.prevent
              @click="addChar(' ')">
            </button>
            <button class="keyboard-key key-dot text-body"
              @pointerdown.prevent="onKeyPointerDown($event, '.')"
              @pointerup.prevent="onKeyPointerUp($event, '.')"
              @pointerleave="onKeyPointerLeave"
              @pointermove="onKeyPointerMove">
              .
            </button>
            <button class="keyboard-key key-dismiss"
              @pointerdown.prevent
              @click="handleClose">
              <SvgIcon name="keyboardHide" :size="24" />
            </button>
          </div>
        </div>

        <!-- Key press popup (shows enlarged character above pressed key) -->
        <div v-if="pressPopup.visible" class="key-press-popup heading-2"
          :style="pressPopup.style">
          {{ pressPopup.char }}
        </div>

        <!-- Long press accent popup (shows row of accent variants) -->
        <div v-if="accentPopup.visible" class="accent-popup"
          :style="accentPopup.style">
          <div v-for="(accent, i) in accentPopup.variants" :key="accent"
            class="accent-option text-body"
            :class="{ 'accent-selected': accentPopup.selectedIndex === i }">
            {{ accent }}
          </div>
        </div>

      </div>
    </Transition>
  </div>
</template>

<script setup>
import { ref, reactive, computed, nextTick, onUnmounted, watch } from 'vue';
import { useI18n } from '@/services/i18n';
import { useVirtualKeyboard } from '@/composables/useVirtualKeyboard';
import { useHardwareConfig } from '@/composables/useHardwareConfig';
import { useTimer } from '@/composables/useTimer';
import SvgIcon from '@/components/ui/SvgIcon.vue';

const { getCurrentLanguage } = useI18n();
const timer = useTimer();
const keyboardState = useVirtualKeyboard();
const { screenResolution } = useHardwareConfig();

const keyboardRef = ref(null);
const displayInput = ref(null);

const isKeyboardVisible = computed(() => keyboardState.isVisible.value);
const keyboardValue = computed({
  get: () => keyboardState.currentValue.value,
  set: (val) => keyboardState.updateValue(val)
});
const keyboardPlaceholder = computed(() => keyboardState.placeholder.value);

// Keyboard internal state
const keyboardMode = ref('abc'); // 'abc', 'numbers', 'symbols'
const isCapsLock = ref(false);
const isShiftHeld = ref(false);
const isUppercase = computed(() => isCapsLock.value || isShiftHeld.value);

// Screen resolution detection
const shouldShowKeyboard = computed(() => {
  const urlParams = new URLSearchParams(window.location.search);
  if (urlParams.get('virtualKeyboard') === 'true') return true;

  const configuredResolution = screenResolution.value;
  const currentWidth = window.innerWidth;
  const currentHeight = window.innerHeight;
  const configWidth = configuredResolution?.width;
  const configHeight = configuredResolution?.height;

  if (!configWidth || !configHeight) return false;
  return currentWidth === configWidth && currentHeight === configHeight;
});

// ===== KEYBOARD LAYOUTS =====
const keyboardLayouts = {
  french: {
    abc: {
      row1: ['a', 'z', 'e', 'r', 't', 'y', 'u', 'i', 'o', 'p'],
      row2: ['q', 's', 'd', 'f', 'g', 'h', 'j', 'k', 'l', 'm'],
      row3: ['w', 'x', 'c', 'v', 'b', 'n', '?', ','],
    },
    numbers: {
      row1: ['1', '2', '3', '4', '5', '6', '7', '8', '9', '0'],
      row2: ['-', '/', ':', ';', '(', ')', '€', '&', '@', '"'],
      row3: [',', '?', '!', "'", '\u2026', '\u014D', '\u30FB', '\u2014'],
    },
    symbols: {
      row1: ['[', ']', '{', '}', '#', '%', '^', '*', '+', '='],
      row2: ['_', '\\', '|', '~', '<', '>', '$', '£', '¥', '\u2E31'],
      row3: [',', '?', '!', "'", '\u2026', '\u014D', '\u30FB', '\u2014'],
    }
  },
  english: {
    abc: {
      row1: ['q', 'w', 'e', 'r', 't', 'y', 'u', 'i', 'o', 'p'],
      row2: ['a', 's', 'd', 'f', 'g', 'h', 'j', 'k', 'l', "'"],
      row3: ['z', 'x', 'c', 'v', 'b', 'n', 'm', ','],
    },
    numbers: {
      row1: ['1', '2', '3', '4', '5', '6', '7', '8', '9', '0'],
      row2: ['-', '/', ':', ';', '(', ')', '$', '&', '@', '"'],
      row3: [',', '?', '!', "'", '\u2026', '\u014D', '\u30FB', '\u2014'],
    },
    symbols: {
      row1: ['[', ']', '{', '}', '#', '%', '^', '*', '+', '='],
      row2: ['_', '\\', '|', '~', '<', '>', '€', '£', '¥', '\u2E31'],
      row3: [',', '?', '!', "'", '\u2026', '\u014D', '\u30FB', '\u2014'],
    }
  },
  spanish: {
    abc: {
      row1: ['q', 'w', 'e', 'r', 't', 'y', 'u', 'i', 'o', 'p'],
      row2: ['a', 's', 'd', 'f', 'g', 'h', 'j', 'k', 'l', 'ñ'],
      row3: ['z', 'x', 'c', 'v', 'b', 'n', 'm', ','],
    },
    numbers: {
      row1: ['1', '2', '3', '4', '5', '6', '7', '8', '9', '0'],
      row2: ['-', '/', ':', ';', '(', ')', '€', '&', '@', '"'],
      row3: [',', '?', '!', "'", '\u2026', '\u014D', '\u30FB', '\u2014'],
    },
    symbols: {
      row1: ['[', ']', '{', '}', '#', '%', '^', '*', '+', '='],
      row2: ['_', '\\', '|', '~', '<', '>', '$', '£', '¥', '\u2E31'],
      row3: [',', '?', '!', "'", '\u2026', '\u014D', '\u30FB', '\u2014'],
    }
  },
  german: {
    abc: {
      row1: ['q', 'w', 'e', 'r', 't', 'z', 'u', 'i', 'o', 'p'],
      row2: ['a', 's', 'd', 'f', 'g', 'h', 'j', 'k', 'l', 'ü'],
      row3: ['y', 'x', 'c', 'v', 'b', 'n', 'm', ','],
    },
    numbers: {
      row1: ['1', '2', '3', '4', '5', '6', '7', '8', '9', '0'],
      row2: ['-', '/', ':', ';', '(', ')', '€', '&', '@', '"'],
      row3: [',', '?', '!', "'", '\u2026', '\u014D', '\u30FB', '\u2014'],
    },
    symbols: {
      row1: ['[', ']', '{', '}', '#', '%', '^', '*', '+', '='],
      row2: ['_', '\\', '|', '~', '<', '>', '$', '£', '¥', '\u2E31'],
      row3: [',', '?', '!', "'", '\u2026', '\u014D', '\u30FB', '\u2014'],
    }
  }
};

// ===== ACCENT MAPS (per-language, native accents only) =====
const accentMaps = {
  french: {
    'a': ['à', 'â', 'æ'],
    'e': ['è', 'é', 'ê', 'ë'],
    'i': ['î', 'ï'],
    'o': ['ô', 'œ'],
    'u': ['ù', 'û', 'ü'],
    'y': ['ÿ'],
    'c': ['ç'],
  },
  english: {
    // English has no native accents
  },
  spanish: {
    'a': ['á'],
    'e': ['é'],
    'i': ['í'],
    'o': ['ó'],
    'u': ['ú', 'ü'],
    'n': ['ñ'],
    '?': ['¿'],
    '!': ['¡'],
  },
  german: {
    'a': ['ä'],
    'o': ['ö'],
    'u': ['ü'],
    's': ['ß'],
  },
};

const accentMap = computed(() => {
  const lang = getCurrentLanguage();
  return accentMaps[lang] || accentMaps.english;
});

// ===== COMPUTED: layout selection =====
const currentLayoutData = computed(() => {
  const lang = getCurrentLanguage();
  if (lang === 'french') return keyboardLayouts.french;
  if (lang === 'spanish') return keyboardLayouts.spanish;
  if (lang === 'german') return keyboardLayouts.german;
  return keyboardLayouts.english;
});

const currentModeLayout = computed(() => currentLayoutData.value[keyboardMode.value]);

const transformKey = (key) => {
  if (keyboardMode.value === 'abc' && isUppercase.value) return key.toUpperCase();
  return key;
};

const currentRow1 = computed(() => currentModeLayout.value.row1.map(transformKey));
const currentRow2 = computed(() => currentModeLayout.value.row2.map(transformKey));
const currentRow3 = computed(() => currentModeLayout.value.row3.map(transformKey));

const modeToggleLabel = computed(() => {
  if (keyboardMode.value === 'abc') return '?123';
  return 'ABC';
});

// ===== POPUP STATE =====
const pressPopup = reactive({ visible: false, char: '', style: {} });
const accentPopup = reactive({ visible: false, variants: [], selectedIndex: -1, style: {} });
let longPressTimer = null;
const LONG_PRESS_DURATION = 500;

// ===== BACKSPACE REPEAT STATE =====
let backspaceInterval = null;

// ===== CORE FUNCTIONS =====
function addChar(char) {
  keyboardValue.value += char;
  if (isShiftHeld.value) isShiftHeld.value = false;
  focusDisplayInput();
}

function backspace() {
  keyboardValue.value = keyboardValue.value.slice(0, -1);
  focusDisplayInput();
}

function toggleCapsLock() {
  isCapsLock.value = !isCapsLock.value;
}

function toggleShift() {
  isShiftHeld.value = !isShiftHeld.value;
}

// Row 3 left: Caps Lock in abc mode, #+=  toggle in numbers/symbols
function handleRow3Left() {
  if (keyboardMode.value === 'abc') {
    toggleCapsLock();
  } else if (keyboardMode.value === 'numbers') {
    keyboardMode.value = 'symbols';
  } else {
    keyboardMode.value = 'numbers';
  }
}

// Row 4 mode: .?123 / ABC toggle between abc and numbers
function toggleMode() {
  if (keyboardMode.value === 'abc') {
    keyboardMode.value = 'numbers';
  } else {
    keyboardMode.value = 'abc';
  }
}

function resetState() {
  keyboardMode.value = 'abc';
  isCapsLock.value = false;
  isShiftHeld.value = false;
  pressPopup.visible = false;
  cleanupAccentListeners();
  timer.clear(longPressTimer);
  timer.clear(backspaceInterval);
}

function handleSubmit() {
  keyboardState.submit();
  resetState();
}

function handleClose() {
  keyboardState.close();
  resetState();
}

// ===== KEY PRESS POPUP =====
// BCR returns visual (post-transform) px, but `left/bottom: Xpx` on the popup
// is interpreted in layout px. When #app has transform: scale (ui_scale), we
// must convert BCR diffs to layout via `BCR / offsetWidth` to keep popups aligned.
function getKeyboardScale() {
  if (!keyboardRef.value) return 1;
  const w = keyboardRef.value.offsetWidth;
  return w ? keyboardRef.value.getBoundingClientRect().width / w : 1;
}

function showPressPopup(event, key) {
  if (!keyboardRef.value) return;

  const keyRect = event.target.getBoundingClientRect();
  const kbRect = keyboardRef.value.getBoundingClientRect();
  const scale = getKeyboardScale();

  const keyLayoutWidth = keyRect.width / scale;
  const popupWidth = Math.max(keyLayoutWidth + 12, 48);
  const left = (keyRect.left - kbRect.left) / scale + (keyLayoutWidth / 2) - (popupWidth / 2);
  const bottom = (kbRect.bottom - keyRect.top) / scale + 6;

  pressPopup.char = key;
  pressPopup.style = {
    left: `${left}px`,
    bottom: `${bottom}px`,
    width: `${popupWidth}px`
  };
  pressPopup.visible = true;
}

// ===== ACCENT POPUP =====
function showAccentPopup(event, key) {
  const lowerKey = key.toLowerCase();
  const variants = accentMap.value[lowerKey];
  if (!variants || variants.length === 0 || !keyboardRef.value) return;

  const mappedVariants = isUppercase.value
    ? variants.map(v => v.toUpperCase())
    : [...variants];

  const keyRect = event.target.getBoundingClientRect();
  const kbRect = keyboardRef.value.getBoundingClientRect();
  const scale = getKeyboardScale();

  const optionWidth = 44;
  const gap = 2;
  const padding = 8;
  const totalWidth = (mappedVariants.length * (optionWidth + gap)) - gap + padding;
  const kbLayoutWidth = kbRect.width / scale;
  const keyLayoutWidth = keyRect.width / scale;

  let left = (keyRect.left - kbRect.left) / scale + (keyLayoutWidth / 2) - (totalWidth / 2);
  left = Math.max(4, Math.min(left, kbLayoutWidth - totalWidth - 4));

  const bottom = (kbRect.bottom - keyRect.top) / scale + 6;

  accentPopup.variants = mappedVariants;
  accentPopup.selectedIndex = -1;
  accentPopup.style = {
    left: `${left}px`,
    bottom: `${bottom}px`
  };
  accentPopup.visible = true;
  pressPopup.visible = false;
}

// ===== POINTER EVENT HANDLERS =====
let activeKey = null;

function onKeyPointerDown(event, key) {
  activeKey = key;
  showPressPopup(event, key);

  const lowerKey = key.toLowerCase();
  if (accentMap.value[lowerKey]) {
    longPressTimer = timer.setTimeout(() => {
      showAccentPopup(event, key);
      // Once accent popup is shown, listen on document for slide-to-select
      document.addEventListener('pointermove', onDocumentPointerMove);
      document.addEventListener('pointerup', onDocumentPointerUp);
      document.addEventListener('pointercancel', onDocumentPointerUp);
    }, LONG_PRESS_DURATION);
  }
}

function onKeyPointerUp(event, key) {
  timer.clear(longPressTimer);
  longPressTimer = null;

  // If accent popup is visible, let the document-level handler manage selection
  if (accentPopup.visible) return;

  addChar(activeKey || key);
  pressPopup.visible = false;
  activeKey = null;
}

function onKeyPointerLeave() {
  // Don't cancel if accent popup is active (finger sliding to popup)
  if (accentPopup.visible) return;

  timer.clear(longPressTimer);
  longPressTimer = null;
  pressPopup.visible = false;
}

function onKeyPointerMove() {
  // Individual key move — not used for accent selection (document handles that)
}

function onDocumentPointerMove(event) {
  if (!accentPopup.visible || !keyboardRef.value) return;

  const kbRect = keyboardRef.value.getBoundingClientRect();
  const scale = getKeyboardScale();
  const popupLeft = parseFloat(accentPopup.style.left); // layout px
  const padding = 4;
  const optionWidth = 44;
  const gap = 2;

  // event.clientX is viewport (post-scale); convert the offset to layout px to match popupLeft/optionWidth.
  const x = (event.clientX - kbRect.left) / scale - popupLeft - padding;
  const index = Math.floor(x / (optionWidth + gap));
  accentPopup.selectedIndex = Math.max(0, Math.min(index, accentPopup.variants.length - 1));
}

function onDocumentPointerUp() {
  if (!accentPopup.visible) return;

  // Only insert if user has slid to an accent (selectedIndex >= 0)
  if (accentPopup.selectedIndex >= 0) {
    const selected = accentPopup.variants[accentPopup.selectedIndex];
    if (selected) addChar(selected);
  }
  cleanupAccentListeners();
  pressPopup.visible = false;
  activeKey = null;
}

function cleanupAccentListeners() {
  accentPopup.visible = false;
  document.removeEventListener('pointermove', onDocumentPointerMove);
  document.removeEventListener('pointerup', onDocumentPointerUp);
  document.removeEventListener('pointercancel', onDocumentPointerUp);
}

// ===== BACKSPACE REPEAT =====
function startBackspaceRepeat() {
  backspace();
  backspaceInterval = timer.setInterval(backspace, 100);
}

function stopBackspaceRepeat() {
  if (backspaceInterval) {
    timer.clear(backspaceInterval);
    backspaceInterval = null;
  }
}

// ===== OUTSIDE CLICK HANDLER =====
function handleOutsideClick(event) {
  if (keyboardRef.value && keyboardRef.value.contains(event.target)) return;
  if (event.target.closest('.input-container')) return;
  handleClose();
}

// ===== ORIGIN ELEMENT VISIBILITY CHECK =====
// Detects when the element that opened the keyboard becomes hidden
// (e.g. parent modal closing with opacity animation, component removed from DOM)
let originCheckInterval = null;

function isAncestorHidden(el) {
  let current = el;
  while (current && current !== document.documentElement) {
    const opacity = parseFloat(getComputedStyle(current).opacity);
    if (opacity < 0.1) return true;
    current = current.parentElement;
  }
  return false;
}

function startOriginCheck() {
  stopOriginCheck();
  originCheckInterval = timer.setInterval(() => {
    const el = keyboardState.originElement.value;
    if (!el) return;
    if (!el.isConnected || isAncestorHidden(el)) {
      handleClose();
    }
  }, 200);
}

function stopOriginCheck() {
  if (originCheckInterval) {
    timer.clear(originCheckInterval);
    originCheckInterval = null;
  }
}

function focusDisplayInput() {
  nextTick(() => {
    if (displayInput.value) {
      displayInput.value.focus({ preventScroll: true });
      const len = displayInput.value.value.length;
      displayInput.value.setSelectionRange(len, len);
    }
  });
}

watch(isKeyboardVisible, (visible) => {
  if (visible) {
    startOriginCheck();
    focusDisplayInput();
    timer.setTimeout(() => {
      document.addEventListener('pointerdown', handleOutsideClick);
    }, 0);
  } else {
    stopOriginCheck();
    document.removeEventListener('pointerdown', handleOutsideClick);
  }
});

onUnmounted(() => {
  stopOriginCheck();
  document.removeEventListener('pointerdown', handleOutsideClick);
  cleanupAccentListeners();
  // longPressTimer / backspaceInterval are auto-cleared by useTimer.
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
  background: var(--color-background-medium-32);
  backdrop-filter: blur(var(--blur-04));
  -webkit-backdrop-filter: blur(var(--blur-04));
  border-radius: var(--radius-07) var(--radius-07) 0 0;
  padding: var(--space-05);
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
  overflow: hidden;
  touch-action: none;
}

.virtual-keyboard::before {
  content: '';
  position: absolute;
  inset: 0;
  padding: 1.5px;
  background: var(--stroke-glass);
  border-radius: var(--radius-07) var(--radius-07) 0 0;
  -webkit-mask:
    linear-gradient(#000 0 0) content-box,
    linear-gradient(#000 0 0);
  -webkit-mask-composite: xor;
  mask-composite: exclude;
  z-index: -1;
  pointer-events: none;
}

/* Header: Input + Backspace — same 10-column grid as rows */
.keyboard-header {
  display: grid;
  grid-template-columns: repeat(10, 1fr);
  gap: var(--space-01);
  align-items: stretch;
}

.keyboard-input-display {
  grid-column: span 9;
}

.keyboard-display-input {
  width: 100%;
  height: 100%;
  padding: var(--space-03) var(--space-04);
  border: 0px;
  box-shadow: inset 0 0 0 1px var(--color-border);
  border-radius: var(--radius-04);
  background: var(--color-background);
  color: var(--color-text);
  font-size: var(--font-size-h3);
  text-align: center;
  caret-color: var(--color-text);
  animation: none;
}

.keyboard-display-input:focus {
  outline: none;
  caret-color: var(--color-text);
  animation: caret-blink 1.2s ease-in-out infinite;
}

@keyframes caret-blink {
  0%, 100% { caret-color: var(--color-text); }
  50% { caret-color: transparent; }
}

/* Keyboard rows */
.keyboard-keys {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
}

.keyboard-row {
  display: grid;
  grid-template-columns: repeat(10, 1fr);
  gap: var(--space-01);
}

/* Base key style — character keys (solo) */
.keyboard-key {
  min-width: 0;
  height: 48px;
  box-sizing: border-box;
  padding: var(--space-02);
  overflow: hidden;
  background: var(--color-background-neutral);
  box-shadow: 0px 1px 0px rgba(0, 0, 0, 0.12);
  border: none;
  border-radius: var(--radius-04);
  color: var(--color-text);
  cursor: pointer;
  user-select: none;
  -webkit-tap-highlight-color: transparent;
  touch-action: manipulation;
  display: flex;
  align-items: center;
  justify-content: center;
}

.keyboard-key:active {
  background: var(--color-background-medium-16);
  box-shadow: none;
}

/* Special keys — shared "button" style */
.key-backspace,
.key-caps,
.key-enter,
.key-shift,
.key-mode,
.key-dismiss {
  background: var(--color-background-contrast-32);
  box-shadow: none;
  color: var(--color-text-contrast);
}

/* Left-side keys: align bottom-left */
.key-caps,
.key-shift,
.key-mode {
  align-items: flex-end;
  justify-content: flex-start;
}

/* Right-side keys: align bottom-right */
.key-backspace,
.key-enter,
.key-dismiss {
  align-items: flex-end;
  justify-content: flex-end;
}

.key-backspace:active,
.key-caps:active,
.key-shift:active,
.key-mode:active,
.key-dismiss:active {
  background: var(--color-background-contrast-80);
}

/* Caps Lock / #+= / 123 */
.key-caps {
  position: relative;
}

/* Enter / Submit arrow */
.key-enter {
  background: var(--color-brand);
  color: var(--color-text-contrast);
}

.key-enter:active {
  opacity: 0.8;
}

/* Space bar — spans 6 columns in the 10-column grid */
.key-space {
  grid-column: span 6;
}

/* Caps Lock active state */
.caps-active {
  background: var(--color-background-contrast-80);
  box-shadow: none;
}

.caps-active :deep(svg) {
  opacity: 0.5;
}

/* Shift active state */
.shift-active {
  background: var(--color-background-contrast-80);
  box-shadow: none;
}

.shift-active :deep(svg) {
  opacity: 0.5;
}

/* Mode active state (numbers/symbols) */
.mode-active {
  background: var(--color-background-contrast-80);
  box-shadow: none;
}

/* ===== KEY PRESS POPUP ===== */
.key-press-popup {
  position: absolute;
  pointer-events: none;
  height: 64px;
  background: var(--color-background-neutral);
  border-radius: var(--radius-03);
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.15);
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--color-text);
  z-index: 10;
}

/* Stem connecting popup to key below */
.key-press-popup::after {
  content: '';
  position: absolute;
  bottom: -8px;
  left: 50%;
  transform: translateX(-50%);
  width: 16px;
  height: 8px;
  background: var(--color-background-neutral);
  clip-path: polygon(0 0, 100% 0, 50% 100%);
}

/* ===== ACCENT POPUP ===== */
.accent-popup {
  position: absolute;
  display: flex;
  gap: 2px;
  background: var(--color-background-neutral);
  border-radius: var(--radius-03);
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.2);
  padding: 4px;
  z-index: 20;
  pointer-events: none;
}

.accent-option {
  width: 44px;
  height: 52px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-02);
  font-size: var(--font-size-h3);
  color: var(--color-text);
}

.accent-selected {
  background: var(--color-brand);
  color: white;
  border-radius: var(--radius-02);
}

/* ===== TRANSITIONS ===== */
.keyboard-enter-active {
  transition: transform var(--transition-normal);
}

.keyboard-leave-active {
  transition: transform var(--transition-normal-leave), opacity var(--transition-normal-leave);
}

.keyboard-enter-from,
.keyboard-leave-to {
  opacity: 0;
  transform: translateX(-50%) translateY(100%);
}

/* ===== MOBILE ADJUSTMENTS ===== */
@media (max-aspect-ratio: 4/3) {
  .virtual-keyboard {
    max-width: 100%;
    border-radius: var(--radius-07) var(--radius-07) 0 0;
  }

  .keyboard-key {
    height: 52px;
  }
}
</style>
