<!-- frontend/src/components/ui/Modal.vue -->
<template>
  <div v-if="isVisible" ref="modalOverlay" class="modal-overlay" @click.self="handleOverlayClick">
    <div class="modal-wrapper">
      <div ref="closeButtonWrapper" class="close-btn-wrapper">
        <IconButton ref="closeButton" icon="close" variant="rounded" size="large"
          aria-label="Fermer" @click="close" />
      </div>

      <div ref="modalContainer" class="modal-container" :style="{ height: containerHeight }"
        @transitionstart="onContainerTransitionStart" @transitionend="onContainerTransitionEnd"
        @transitioncancel="onContainerTransitionEnd">
        <!-- Content with animated height -->
        <div ref="modalContent" class="modal-content" :class="{ 'overflow-transitioning': isHeightTransitioning }">
          <div ref="contentInner" class="modal-content-inner">
            <slot></slot>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, watch, nextTick, provide } from 'vue';
import IconButton from './IconButton.vue';
import { useAnimatedHeight } from '@/composables/useAnimatedHeight';

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
const closeButtonWrapper = ref(null);
const contentInner = ref(null);

// Animated height composable - observe contentInner, add modal-content padding
const { containerHeight, resetFirstResize, requestHeightDelta } = useAnimatedHeight(contentInner, {
  threshold: 2,
  skipFirstResize: true,
  getExtraHeight: () => {
    if (!modalContent.value) return 0;
    const style = getComputedStyle(modalContent.value);
    return parseFloat(style.paddingTop) + parseFloat(style.paddingBottom);
  },
  getMaxHeight: () => {
    if (!modalOverlay.value) return Infinity;
    const style = getComputedStyle(modalOverlay.value);
    const paddingTop = parseFloat(style.paddingTop);
    const paddingBottom = parseFloat(style.paddingBottom);
    const bounceMargin = 24;
    return modalOverlay.value.clientHeight - paddingTop - paddingBottom - bounceMargin;
  }
});

// Animation state
const isVisible = ref(false);
const isAnimating = ref(false);

// Height transition tracking — prevent modal-content from clipping
// leaving content during the container height spring animation
const isHeightTransitioning = ref(false);

function onContainerTransitionStart(e) {
  if (e.propertyName === 'height') {
    isHeightTransitioning.value = true;
  }
}

function onContainerTransitionEnd(e) {
  if (e.propertyName === 'height') {
    isHeightTransitioning.value = false;
  }
}

// Provide a function to reset scroll position (for multi-level modal navigation)
provide('modalResetScroll', () => {
  if (modalContent.value) {
    modalContent.value.scrollTop = 0;
  }
});

// Provide the modalContent ref for height calculations
provide('modalContentRef', modalContent);

// Provide resetFirstResize for children to signal data loaded
provide('modalResetFirstResize', resetFirstResize);

// Provide requestHeightDelta for children to pre-announce height changes before animations
provide('modalRequestHeightDelta', requestHeightDelta);

// Provide a way to safely restore scroll during height transitions.
// Forces overflow-y: auto inline (overriding overflow-transitioning class) so
// scrollTop can be set immediately. Safe because the leaving element is already
// gone by this point — no clipping concern. Cleans up after height transition.
provide('modalDeferScrollRestore', (callback) => {
  const el = modalContent.value;
  if (!el) { callback(); return; }

  // Force scrollable so scrollTop can be set during overflow:visible
  el.style.overflowY = 'auto';
  callback();

  // Remove inline override after height transition ends (or next frame if none)
  const cleanup = () => { if (el) el.style.overflowY = ''; };
  if (isHeightTransitioning.value) {
    const unwatch = watch(isHeightTransitioning, (val) => {
      if (!val) { unwatch(); cleanup(); }
    });
  } else {
    requestAnimationFrame(cleanup);
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

  // Start a new 120-second timer
  inactivityTimer = setTimeout(() => {
    close();
  }, 120000); // 120 seconds before auto-close
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
  resetFirstResize(); // Reset animated height to skip first animation

  isAnimating.value = true;
  isVisible.value = true;

  await nextTick();

  if (!modalContainer.value || !modalOverlay.value || !closeButtonWrapper.value) return;

  // Initial overlay state (invisible)
  modalOverlay.value.style.transition = 'none';
  modalOverlay.value.style.opacity = '0';

  // Initial container state (invisible and scaled down)
  modalContainer.value.style.transition = 'none';
  modalContainer.value.style.opacity = '0';
  modalContainer.value.style.transform = 'scale(0.9)';

  // Initial close button state (invisible and higher position)
  closeButtonWrapper.value.style.transition = 'none';
  closeButtonWrapper.value.classList.remove('visible');
  closeButton.value.$el.style.transition = 'none';
  closeButton.value.$el.style.opacity = '0';

  // Force reflow
  modalContainer.value.offsetHeight;

  // Overlay enter animation (immediate)
  const overlayTimeout = setTimeout(() => {
    if (!modalOverlay.value) return;
    modalOverlay.value.style.transition = `opacity ${ANIMATION_TIMINGS.overlayDuration}ms ease-out`;
    modalOverlay.value.style.opacity = '1';
  }, ANIMATION_TIMINGS.overlayDelay);
  animationTimeouts.push(overlayTimeout);

  // Container enter animation (scale via --transition-spring, opacity via ease-out)
  const containerTimeout = setTimeout(() => {
    if (!modalContainer.value) return;
    modalContainer.value.style.transition = 'transform var(--transition-spring), opacity 400ms ease-out, height var(--transition-spring)';
    modalContainer.value.style.opacity = '1';
    modalContainer.value.style.transform = 'scale(1)';
  }, ANIMATION_TIMINGS.containerDelay);
  animationTimeouts.push(containerTimeout);

  // Delayed close button animation (wrapper slides, button fades independently)
  const closeButtonTimeout = setTimeout(() => {
    if (!closeButtonWrapper.value || !closeButton.value) return;
    closeButtonWrapper.value.style.transition = 'transform var(--transition-spring)';
    closeButtonWrapper.value.classList.add('visible');
    closeButton.value.$el.style.transition = `opacity ${ANIMATION_TIMINGS.closeButtonDuration}ms ease-out`;
    closeButton.value.$el.style.opacity = '1';
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

  if (!modalContainer.value || !modalOverlay.value || !closeButtonWrapper.value) return;

  // Exit animation with ease-out for closing
  const overlayCloseTimeout = setTimeout(() => {
    if (!modalOverlay.value) return;
    modalOverlay.value.style.transition = `opacity ${ANIMATION_TIMINGS.closeOverlayDuration}ms ease-out`;
    modalOverlay.value.style.opacity = '0';
  }, ANIMATION_TIMINGS.closeOverlayDelay);
  animationTimeouts.push(overlayCloseTimeout);

  const containerCloseTimeout = setTimeout(() => {
    if (!modalContainer.value) return;
    modalContainer.value.style.transition = `transform ${ANIMATION_TIMINGS.closeContainerDuration}ms ease-out, opacity ${ANIMATION_TIMINGS.closeContainerDuration}ms ease-out, height ${ANIMATION_TIMINGS.closeContainerDuration}ms ease-out`;
    modalContainer.value.style.opacity = '0';
    modalContainer.value.style.transform = 'scale(0.9)';
  }, ANIMATION_TIMINGS.closeContainerDelay);
  animationTimeouts.push(containerCloseTimeout);

  const closeButtonCloseTimeout = setTimeout(() => {
    if (!closeButtonWrapper.value) return;
    closeButtonWrapper.value.style.transition = 'none';
    closeButtonWrapper.value.classList.remove('visible');
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

// User activity handler (throttled - auto-close is 120s so 1s granularity is plenty)
let lastModalActivity = 0;
const MODAL_ACTIVITY_THROTTLE_MS = 1000;

function handleUserActivity() {
  const now = Date.now();
  if (now - lastModalActivity < MODAL_ACTIVITY_THROTTLE_MS) return;
  lastModalActivity = now;
  resetInactivityTimer();
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

// Add user activity listeners (pointerdown/touchstart sufficient for 120s auto-close)
function addActivityListeners() {
  if (!modalOverlay.value) return;

  modalOverlay.value.addEventListener('pointerdown', handleUserActivity, { passive: true });
  modalOverlay.value.addEventListener('wheel', handleUserActivity, { passive: true });
  modalOverlay.value.addEventListener('touchstart', handleUserActivity, { passive: true });
}

// Remove user activity listeners
function removeActivityListeners() {
  if (!modalOverlay.value) return;

  modalOverlay.value.removeEventListener('pointerdown', handleUserActivity);
  modalOverlay.value.removeEventListener('wheel', handleUserActivity);
  modalOverlay.value.removeEventListener('touchstart', handleUserActivity);
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
  backdrop-filter: blur(var(--blur-03));
  -webkit-backdrop-filter: blur(var(--blur-03));
  display: flex;
  align-items: flex-start;
  justify-content: center;
  z-index: 5000;
  padding: var(--space-07) var(--space-04) var(--space-05) var(--space-04);
  opacity: 0;
}

.modal-wrapper {
  position: relative;
  width: 100%;
  max-width: 768px;
  max-height: 100%;
}

.modal-container {
  position: relative;
  background: var(--color-background-neutral-50);
  border-radius: var(--radius-08);
  width: 100%;
  max-height: 100%;
  display: flex;
  flex-direction: column;
  opacity: 0;
  overflow: hidden;
  transition: height var(--transition-spring-slow);
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

.close-btn-wrapper {
  position: absolute;
  top: 0;
  right: calc(-1 * var(--space-04) - 60px);
  transform: translateY(-24px);
  visibility: hidden;
}

.close-btn-wrapper.visible {
  transform: translateY(0);
  visibility: visible;
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

/* Prevent clipping during container height spring (crossfade transitions) */
.modal-content.overflow-transitioning {
  overflow: visible;
}

.modal-content-inner {
  display: flex;
  flex-direction: column;
}

/* Responsive */
@media (max-aspect-ratio: 4/3) {
  ::-webkit-scrollbar {
    display: none;
  }

  .close-btn-wrapper {
    top: calc(-1 * var(--space-03) - 52px);
    left: 50%;
    right: auto;
    transform: translateX(-50%) translateY(-24px);
  }

  .close-btn-wrapper.visible {
    transform: translateX(-50%) translateY(0);
  }

  .modal-overlay {
    align-items: flex-start;
    padding: calc(76px + env(safe-area-inset-top, 0px) - min(env(safe-area-inset-top, 0px), var(--space-03))) var(--space-02) var(--space-02) var(--space-02);
  }

  .modal-wrapper {
    max-width: none;
  }

  .modal-container,
  .modal-content,
  .modal-container::before {
    border-radius: var(--radius-07);
  }

}
</style>