<!-- frontend/src/components/ui/Modal.vue -->
<template>
  <div v-if="isVisible" ref="modalOverlay" class="modal-overlay" @click.self="handleOverlayClick">
    <div ref="modalContainer" class="modal-container">
      <IconButton ref="closeButton" class="close-btn-position" icon="close" variant="rounded" size="large"
        aria-label="Fermer" @click="close" />

      <!-- Content -->
      <div ref="modalContent" class="modal-content" @pointerdown="handlePointerDown" @pointermove="handlePointerMove"
        @pointerup="handlePointerUp" @pointercancel="handlePointerUp">
        <slot></slot>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, watch, nextTick, provide } from 'vue';
import IconButton from './IconButton.vue';

const props = defineProps({
  isOpen: {
    type: Boolean,
    required: true
  },
  closeOnOverlay: {
    type: Boolean,
    default: true
  }
});

const emit = defineEmits(['close']);

// References to modal elements
const modalContent = ref(null);
const modalContainer = ref(null);
const modalOverlay = ref(null);
const closeButton = ref(null);

// Animation state
const isVisible = ref(false);
const isAnimating = ref(false);

// Provide a function to reset scroll position (for multi-level modal navigation)
provide('modalResetScroll', () => {
  if (modalContent.value) {
    modalContent.value.scrollTop = 0;
  }
});

// Variables to cancel ongoing timeouts
let animationTimeouts = [];
let inactivityTimer = null;

// Utility to clear all timeouts
function clearAllTimeouts() {
  animationTimeouts.forEach(timeout => clearTimeout(timeout));
  animationTimeouts = [];
}

// Clear the inactivity timer
function clearInactivityTimer() {
  if (inactivityTimer) {
    clearTimeout(inactivityTimer);
    inactivityTimer = null;
  }
}

// Reset the inactivity timer
function resetInactivityTimer() {
  clearInactivityTimer();

  // Start a new 60-second timer
  inactivityTimer = setTimeout(() => {
    close();
  }, 60000); // 60 seconds
}

const ANIMATION_TIMINGS = {
  // Opening delays
  overlayDelay: 0,
  containerDelay: 100,
  closeButtonDelay: 600,

  // Opening durations
  overlayDuration: 400,
  closeButtonDuration: 400,

  // Closing delays
  closeOverlayDelay: 0,
  closeContainerDelay: 0,
  closeButtonDelayOut: 0,

  // Closing durations
  closeOverlayDuration: 300,
  closeContainerDuration: 200,
  closeButtonDurationOut: 200
};

// Pointer scroll variables
let isDragging = false;
let startY = 0;
let startScrollTop = 0;
let pointerId = null;
let hasMoved = false;

function close() {
  emit('close');
}

function handleOverlayClick() {
  if (props.closeOnOverlay) {
    close();
  }
}

// === ANIMATIONS ===
async function openModal() {
  clearAllTimeouts();

  isAnimating.value = true;
  isVisible.value = true;

  await nextTick();

  if (!modalContainer.value || !modalOverlay.value || !closeButton.value) return;

  // Initial overlay state (invisible)
  modalOverlay.value.style.transition = 'none';
  modalOverlay.value.style.opacity = '0';

  // Initial container state (invisible and lower position like the dock)
  modalContainer.value.style.transition = 'none';
  modalContainer.value.style.opacity = '0';
  modalContainer.value.style.transform = 'translateY(80px) scale(0.85)';

  // Initial close button state (invisible and higher position)
  closeButton.value.$el.style.transition = 'none';
  closeButton.value.$el.style.opacity = '0';
  closeButton.value.$el.style.transform = 'translateX(-50%) translateY(-24px)';

  // Force reflow
  modalContainer.value.offsetHeight;

  // Overlay enter animation (immediate)
  const overlayTimeout = setTimeout(() => {
    if (!modalOverlay.value) return;
    modalOverlay.value.style.transition = `opacity ${ANIMATION_TIMINGS.overlayDuration}ms ease-out`;
    modalOverlay.value.style.opacity = '1';
  }, ANIMATION_TIMINGS.overlayDelay);
  animationTimeouts.push(overlayTimeout);

  // Container enter animation (uses --transition-spring like the dock)
  const containerTimeout = setTimeout(() => {
    if (!modalContainer.value) return;
    modalContainer.value.style.transition = 'transform var(--transition-spring), opacity 400ms ease-out';
    modalContainer.value.style.opacity = '1';
    modalContainer.value.style.transform = 'translateY(0) scale(1)';
  }, ANIMATION_TIMINGS.containerDelay);
  animationTimeouts.push(containerTimeout);

  // Delayed close button animation (uses --transition-spring)
  const closeButtonTimeout = setTimeout(() => {
    if (!closeButton.value || !closeButton.value.$el) return;
    closeButton.value.$el.style.transition = `transform var(--transition-spring), opacity ${ANIMATION_TIMINGS.closeButtonDuration}ms ease-out`;
    closeButton.value.$el.style.opacity = '1';
    closeButton.value.$el.style.transform = 'translateX(-50%) translateY(0)';
  }, ANIMATION_TIMINGS.closeButtonDelay);
  animationTimeouts.push(closeButtonTimeout);

  // Wait for the end of the animation
  const totalDuration = Math.max(
    ANIMATION_TIMINGS.closeButtonDelay + ANIMATION_TIMINGS.closeButtonDuration,
    ANIMATION_TIMINGS.containerDelay + 600,
    ANIMATION_TIMINGS.overlayDelay + ANIMATION_TIMINGS.overlayDuration
  );

  const finalTimeout = setTimeout(() => {
    isAnimating.value = false;
    // Add activity listeners and start the inactivity timer
    addActivityListeners();
    resetInactivityTimer();
  }, totalDuration);
  animationTimeouts.push(finalTimeout);
}

async function closeModal() {
  clearAllTimeouts();
  clearInactivityTimer();
  removeActivityListeners();

  isAnimating.value = true;

  if (!modalContainer.value || !modalOverlay.value || !closeButton.value) return;

  // Exit animation with ease-out for closing
  const overlayCloseTimeout = setTimeout(() => {
    if (!modalOverlay.value) return;
    modalOverlay.value.style.transition = `opacity ${ANIMATION_TIMINGS.closeOverlayDuration}ms ease-out`;
    modalOverlay.value.style.opacity = '0';
  }, ANIMATION_TIMINGS.closeOverlayDelay);
  animationTimeouts.push(overlayCloseTimeout);

  const containerCloseTimeout = setTimeout(() => {
    if (!modalContainer.value) return;
    modalContainer.value.style.transition = `transform ${ANIMATION_TIMINGS.closeContainerDuration}ms ease-out, opacity ${ANIMATION_TIMINGS.closeContainerDuration}ms ease-out`;
    modalContainer.value.style.opacity = '0';
    modalContainer.value.style.transform = 'translateY(var(--space-08)) scale(0.95)';
  }, ANIMATION_TIMINGS.closeContainerDelay);
  animationTimeouts.push(containerCloseTimeout);

  const closeButtonCloseTimeout = setTimeout(() => {
    if (!closeButton.value || !closeButton.value.$el) return;
    closeButton.value.$el.style.transition = `opacity ${ANIMATION_TIMINGS.closeButtonDurationOut}ms ease-out`;
    closeButton.value.$el.style.opacity = '0';
    closeButton.value.$el.style.transform = 'translateX(-50%)';
  }, ANIMATION_TIMINGS.closeButtonDelayOut);
  animationTimeouts.push(closeButtonCloseTimeout);

  // Wait for the end of the animation
  const totalCloseDuration = Math.max(
    ANIMATION_TIMINGS.closeOverlayDelay + ANIMATION_TIMINGS.closeOverlayDuration,
    ANIMATION_TIMINGS.closeContainerDelay + ANIMATION_TIMINGS.closeContainerDuration,
    ANIMATION_TIMINGS.closeButtonDelayOut + ANIMATION_TIMINGS.closeButtonDurationOut
  );

  const finalCloseTimeout = setTimeout(() => {
    isVisible.value = false;
    isAnimating.value = false;
  }, totalCloseDuration);
  animationTimeouts.push(finalCloseTimeout);
}

// User activity handler
function handleUserActivity() {
  resetInactivityTimer();
}

// Pointer scroll handling
function handlePointerDown(event) {
  if (!modalContent.value) return;

  // Disable pointer scroll on touch devices (mobile)
  // Raspberry with mouse (pointer: fine) will keep manual scroll
  const isTouchDevice = window.matchMedia('(pointer: coarse)').matches;
  if (isTouchDevice) {
    return;
  }

  // Exclude sliders and other interactive controls
  const isSlider = event.target.closest('input[type="range"]');
  const isButton = event.target.closest('button');
  const isInput = event.target.closest('input, select, textarea');

  if (isSlider || isButton || isInput) {
    return;
  }

  isDragging = true;
  hasMoved = false;
  pointerId = event.pointerId;
  startY = event.clientY;
  startScrollTop = modalContent.value.scrollTop;
}

function handlePointerMove(event) {
  if (!isDragging || event.pointerId !== pointerId || !modalContent.value) return;

  const deltaY = Math.abs(startY - event.clientY);

  if (deltaY > 5) {
    hasMoved = true;

    if (!modalContent.value.hasPointerCapture(event.pointerId)) {
      modalContent.value.setPointerCapture(event.pointerId);
    }

    event.preventDefault();

    const scrollDelta = startY - event.clientY;
    modalContent.value.scrollTop = startScrollTop + scrollDelta;
  }
}

function handlePointerUp(event) {
  if (event.pointerId === pointerId) {
    isDragging = false;
    pointerId = null;
    hasMoved = false;

    if (modalContent.value && modalContent.value.hasPointerCapture(event.pointerId)) {
      modalContent.value.releasePointerCapture(event.pointerId);
    }
  }
}

// Escape handling
function handleKeydown(event) {
  if (event.key === 'Escape' && props.isOpen) {
    close();
  }
}

// Block body scroll when modal is open
function toggleBodyScroll(isOpen) {
  if (isOpen) {
    document.body.style.overflow = 'hidden';
  } else {
    document.body.style.overflow = '';
  }
}

// Add user activity listeners
function addActivityListeners() {
  if (!modalOverlay.value) return;

  modalOverlay.value.addEventListener('pointermove', handleUserActivity, { passive: true });
  modalOverlay.value.addEventListener('pointerdown', handleUserActivity, { passive: true });
  modalOverlay.value.addEventListener('wheel', handleUserActivity, { passive: true });
  modalOverlay.value.addEventListener('touchstart', handleUserActivity, { passive: true });
  modalOverlay.value.addEventListener('touchmove', handleUserActivity, { passive: true });
}

// Remove user activity listeners
function removeActivityListeners() {
  if (!modalOverlay.value) return;

  modalOverlay.value.removeEventListener('pointermove', handleUserActivity);
  modalOverlay.value.removeEventListener('pointerdown', handleUserActivity);
  modalOverlay.value.removeEventListener('wheel', handleUserActivity);
  modalOverlay.value.removeEventListener('touchstart', handleUserActivity);
  modalOverlay.value.removeEventListener('touchmove', handleUserActivity);
}

// Watcher for animations
watch(() => props.isOpen, async (newValue) => {
  if (newValue) {
    toggleBodyScroll(true);
    await openModal();
  } else {
    await closeModal();
    toggleBodyScroll(false);
  }
});

onMounted(() => {
  document.addEventListener('keydown', handleKeydown, { passive: true });
});

onUnmounted(() => {
  document.removeEventListener('keydown', handleKeydown);
  document.body.style.overflow = '';
  clearAllTimeouts();
  clearInactivityTimer();
  removeActivityListeners();
});
</script>

<style scoped>
::-webkit-scrollbar {
  display: none;
}

.modal-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: var(--color-background-medium-32);
  backdrop-filter: blur(32px);
  display: flex;
  align-items: flex-start;
  justify-content: center;
  z-index: 5000;
  padding: 48px var(--space-04) var(--space-07) var(--space-04);
  opacity: 0;
}

.modal-container {
  position: relative;
  background: var(--color-background-neutral-50);
  border-radius: var(--radius-08);
  width: 100%;
  max-width: 768px;
  max-height: 100%;
  display: flex;
  flex-direction: column;
  opacity: 0;
}

.modal-container::before {
  content: '';
  position: absolute;
  inset: 0;
  padding: 2px;
  opacity: 0.8;
  background: var(--stroke-glass);
  border-radius: var(--radius-08);
  -webkit-mask:
    linear-gradient(#000 0 0) content-box,
    linear-gradient(#000 0 0);
  -webkit-mask-composite: xor;
  mask-composite: exclude;
  z-index: -1;
  pointer-events: none;
}

.close-btn-position {
  position: absolute;
  top: 0;
  right: calc(-1 * var(--space-04) - var(--space-09));
  opacity: 0;
  transform: translateY(-24px);
}

.modal-content {
  overflow-y: auto;
  padding: var(--space-04);
  display: flex;
  flex-direction: column;
  min-height: 0;
  touch-action: pan-y;
  border-radius: var(--radius-08);
}


/* Responsive */
@media (max-aspect-ratio: 4/3) {
  ::-webkit-scrollbar {
    display: none;
  }

  .close-btn-position {
    position: absolute;
    top: calc(-1 * var(--space-05) - var(--space-08));
    left: 50%;
    transform: translateX(-50%) translateY(-24px);
  }

  .modal-overlay {
    align-items: flex-start;
    padding: 80px var(--space-02) var(--space-02) var(--space-02);
  }

  .modal-container {
    max-width: none;
  }
  
  .modal-container, 
  .modal-content,
  .modal-container::before {
    border-radius: var(--radius-07);
  }

}



.ios-app .modal-overlay {
  padding: 112px var(--space-02) var(--space-02) var(--space-02);
}
</style>