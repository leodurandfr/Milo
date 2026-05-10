<!-- frontend/src/components/ui/Modal.vue -->
<template>
  <div v-if="isVisible" ref="modalOverlay" class="modal-overlay" @click.self="handleOverlayClick">
    <div class="modal-wrapper">
      <div ref="closeButtonWrapper" class="close-btn-wrapper">
        <IconButton ref="closeButton" icon="close" variant="rounded" size="large"
          aria-label="Fermer" @click="close" />
      </div>

      <div ref="modalContainer" class="modal-container"
        :style="{ height: containerHeight }"
        @transitionstart.self="onContainerTransitionStart" @transitionend.self="onContainerTransitionEnd"
        @transitioncancel.self="onContainerTransitionEnd">
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
import { modalDebugLog, modalDebugTrace } from '@/services/modalDebug';

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

// Height transition tracking — used by modalDeferScrollRestore to wait
// for the height spring to settle before cleaning up scroll overrides
const isHeightTransitioning = ref(false);

function onContainerTransitionStart(e) {
  if (e.propertyName === 'height') {
    // [DEBUG-SCROLL]
    const el = modalContent.value;
    modalDebugLog(`[Modal/transitionstart height] ${performance.now().toFixed(0)}ms` + (el ? ` — scrollTop=${el.scrollTop} clientH=${el.clientHeight} scrollH=${el.scrollHeight}` : ''));
    isHeightTransitioning.value = true;
  }
}

function onContainerTransitionEnd(e) {
  if (e.propertyName === 'height') {
    // [DEBUG-SCROLL]
    const el = modalContent.value;
    modalDebugLog(`[Modal/transitionend height]   ${performance.now().toFixed(0)}ms` + (el ? ` — scrollTop=${el.scrollTop} clientH=${el.clientHeight} scrollH=${el.scrollHeight}` : ''));
    isHeightTransitioning.value = false;
  }
}

// Provide the modalContent ref for height calculations
provide('modalContentRef', modalContent);

// Provide resetFirstResize for children to signal data loaded
provide('modalResetFirstResize', resetFirstResize);

// Provide requestHeightDelta for children to pre-announce height changes before animations
provide('modalRequestHeightDelta', requestHeightDelta);

// Provide contentInner ref so children can measure exact height deltas
provide('modalContentInnerRef', contentInner);

// [DEBUG-SCROLL] Trace every scroll change with stack trace
function logScroll(e) {
  const el = e.currentTarget;
  modalDebugLog(`[Modal/scroll-event] ${performance.now().toFixed(0)}ms — scrollTop=${el.scrollTop} clientH=${el.clientHeight} scrollH=${el.scrollHeight} overflowY-inline="${el.style.overflowY}" hasClass=${el.classList.contains('overflow-transitioning')}`);
  modalDebugTrace('[Modal/scroll-event] trace');
}

// [DEBUG-SCROLL] Log scrollTop / clientH / scrollH at every height change
watch(containerHeight, (newH, oldH) => {
  const el = modalContent.value;
  if (!el) return;
  modalDebugLog(`[Modal/containerHeight] ${performance.now().toFixed(0)}ms — ${oldH} → ${newH} — scrollTop=${el.scrollTop} clientH=${el.clientHeight} scrollH=${el.scrollHeight} maxScroll=${el.scrollHeight - el.clientHeight} overflowY-inline="${el.style.overflowY}" hasClass=${el.classList.contains('overflow-transitioning')}`);
});

watch(isHeightTransitioning, (val) => {
  const el = modalContent.value;
  modalDebugLog(`[Modal/isHeightTransitioning] ${performance.now().toFixed(0)}ms — → ${val}` + (el ? ` — scrollTop=${el.scrollTop} clientH=${el.clientHeight} scrollH=${el.scrollHeight} overflowY-inline="${el.style.overflowY}" hasClass=${el.classList.contains('overflow-transitioning')}` : ''));
});

// Defer the entire finalize callback (Phases 1+2+3 of useViewTransition.onAfterLeave)
// until the height spring has fully settled. This avoids the spring's overshoot
// peak (where modal-content.clientHeight ≥ scrollHeight, maxScroll = 0) silently
// clamping the scrollTop write to 0 on back navigation with positive targetScroll.
let deferScrollWatcher = null;

// Generation counter: every queued finalize captures the generation at queue time
// and aborts if cancelDeferredFinalize has bumped the counter since. This guards
// the two-frame rAF gate, which the watcher cancel alone cannot abort once queued.
let deferGeneration = 0;

// Cancel any pending deferred finalize. Called by useViewTransition.prepareNavigation()
// when a new navigation starts, to avoid a stale finalize firing on transitioncancel
// of the interrupted previous transition (which would corrupt the new transition's
// state and potentially overwrite scrollTop with the previous nav's target).
function cancelDeferredFinalize() {
  deferGeneration++;
  if (deferScrollWatcher) {
    modalDebugLog(`[Modal/deferScrollRestore] ${performance.now().toFixed(0)}ms CANCELLED — pending finalize cancelled by new navigation`);
    deferScrollWatcher();
    deferScrollWatcher = null;
  }
}
provide('modalCancelDeferredFinalize', cancelDeferredFinalize);

provide('modalDeferScrollRestore', (callback) => {
  const el = modalContent.value;
  if (!el) { callback(); return; }

  // Capture the generation at queue time. cancelDeferredFinalize bumps the counter,
  // so any in-flight rAF or watcher callback aborts when its captured gen no longer
  // matches the current one. This is the only reliable way to cancel the two-frame
  // rAF gate below; cancelling the watcher alone leaves rAFs queued.
  const queuedGen = ++deferGeneration;

  // [DEBUG-SCROLL]
  modalDebugLog(`[Modal/deferScrollRestore] ${performance.now().toFixed(0)}ms QUEUED — scrollTop=${el.scrollTop} clientH=${el.clientHeight} scrollH=${el.scrollHeight} overflowY-inline="${el.style.overflowY}" hasClass=${el.classList.contains('overflow-transitioning')}`);

  if (deferScrollWatcher) { deferScrollWatcher(); deferScrollWatcher = null; }

  const isStale = () => queuedGen !== deferGeneration;

  const runFinalize = () => {
    if (isStale()) return;
    // [DEBUG-SCROLL]
    modalDebugLog(`[Modal/deferScrollRestore] ${performance.now().toFixed(0)}ms RUN FINALIZE — scrollTop=${el.scrollTop} clientH=${el.clientHeight} scrollH=${el.scrollHeight} overflowY-inline="${el.style.overflowY}" hasClass=${el.classList.contains('overflow-transitioning')}`);
    // Force scrollable so scrollTop write inside callback isn't blocked by
    // the .overflow-transitioning class (which is still applied this microtask
    // tick, before Vue's render flush removes it).
    el.style.overflowY = 'auto';
    callback();
    // [DEBUG-SCROLL]
    modalDebugLog(`[Modal/deferScrollRestore] ${performance.now().toFixed(0)}ms POST FINALIZE — scrollTop=${el.scrollTop} clientH=${el.clientHeight} scrollH=${el.scrollHeight} overflowY-inline="${el.style.overflowY}" hasClass=${el.classList.contains('overflow-transitioning')}`);
    // Cleanup: remove inline override on the next frame, after the class has been
    // removed by Vue's render flush.
    requestAnimationFrame(() => {
      if (el) {
        el.style.overflowY = '';
        modalDebugLog(`[Modal/deferScrollRestore] ${performance.now().toFixed(0)}ms CLEANUP — scrollTop=${el.scrollTop} clientH=${el.clientHeight} scrollH=${el.scrollHeight} overflowY-inline="${el.style.overflowY}" hasClass=${el.classList.contains('overflow-transitioning')}`);
      }
    });
  };

  // Wait 2 frames so transitionstart has had a chance to fire (it's async after
  // the inline-style change), then check whether a height spring is in progress.
  // Both rAFs and the watcher path bail via isStale() if cancelled in the meantime.
  requestAnimationFrame(() => {
    if (isStale()) return;
    requestAnimationFrame(() => {
      if (isStale()) return;
      if (isHeightTransitioning.value) {
        deferScrollWatcher = watch(isHeightTransitioning, (val) => {
          if (!val) {
            if (deferScrollWatcher) { deferScrollWatcher(); deferScrollWatcher = null; }
            runFinalize();
          }
        });
      } else {
        runFinalize();
      }
    });
  });
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
  containerDelay: 80,
  closeButtonDelay: 500,

  // Opening durations
  overlayDuration: 350,
  closeButtonDuration: 350,

  // Closing delays
  closeOverlayDelay: 0,
  closeContainerDelay: 0,
  closeButtonDelayOut: 0,

  // Closing durations
  closeOverlayDuration: 250,
  closeContainerDuration: 180,
  closeButtonDurationOut: 150
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
  modalContainer.value.style.transform = 'scale(0.95)';

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
    modalOverlay.value.style.transition = `opacity ${ANIMATION_TIMINGS.overlayDuration}ms var(--easeOutCubic)`;
    modalOverlay.value.style.opacity = '1';
  }, ANIMATION_TIMINGS.overlayDelay);
  animationTimeouts.push(overlayTimeout);

  // Container enter animation (scale via --transition-spring, opacity via ease-out)
  const containerTimeout = setTimeout(() => {
    if (!modalContainer.value) return;
    modalContainer.value.style.transition = 'transform var(--transition-spring-snappy), opacity 300ms var(--easeOutCubic), height var(--transition-spring-snappy)';
    modalContainer.value.style.opacity = '1';
    modalContainer.value.style.transform = 'scale(1)';
  }, ANIMATION_TIMINGS.containerDelay);
  animationTimeouts.push(containerTimeout);

  // Delayed close button animation (wrapper slides, button fades independently)
  const closeButtonTimeout = setTimeout(() => {
    if (!closeButtonWrapper.value || !closeButton.value) return;
    closeButtonWrapper.value.style.transition = 'transform var(--transition-spring-snappy)';
    closeButtonWrapper.value.classList.add('visible');
    closeButton.value.$el.style.transition = `opacity ${ANIMATION_TIMINGS.closeButtonDuration}ms var(--easeOutCubic)`;
    closeButton.value.$el.style.opacity = '1';
  }, ANIMATION_TIMINGS.closeButtonDelay);
  animationTimeouts.push(closeButtonTimeout);

  // Wait for the end of the animation
  const totalDuration = Math.max(
    ANIMATION_TIMINGS.closeButtonDelay + ANIMATION_TIMINGS.closeButtonDuration,
    ANIMATION_TIMINGS.containerDelay + 860,
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
    modalOverlay.value.style.transition = `opacity ${ANIMATION_TIMINGS.closeOverlayDuration}ms var(--easeOutCubic)`;
    modalOverlay.value.style.opacity = '0';
  }, ANIMATION_TIMINGS.closeOverlayDelay);
  animationTimeouts.push(overlayCloseTimeout);

  const containerCloseTimeout = setTimeout(() => {
    if (!modalContainer.value) return;
    modalContainer.value.style.transition = `transform ${ANIMATION_TIMINGS.closeContainerDuration}ms var(--easeOutCubic), opacity ${ANIMATION_TIMINGS.closeContainerDuration}ms var(--easeOutCubic), height ${ANIMATION_TIMINGS.closeContainerDuration}ms var(--easeOutCubic)`;
    modalContainer.value.style.opacity = '0';
    modalContainer.value.style.transform = 'scale(0.98)';
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

// [DEBUG-SCROLL] Attach scroll listener as soon as modalContent exists
watch(modalContent, (el, prev) => {
  if (prev) prev.removeEventListener('scroll', logScroll);
  if (el) el.addEventListener('scroll', logScroll, { passive: true });
});

onUnmounted(() => {
  if (deferScrollWatcher) { deferScrollWatcher(); deferScrollWatcher = null; }
  // [DEBUG-SCROLL] cleanup scroll listener
  if (modalContent.value) modalContent.value.removeEventListener('scroll', logScroll);
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

/* Prevent scrollbar flicker during container height spring.
   overflow:hidden (not visible) preserves scrollTop during view transitions. */
.modal-content.overflow-transitioning {
  overflow: hidden;
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