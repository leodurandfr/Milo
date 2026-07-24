<!-- frontend/src/components/ui/Modal.vue -->
<template>
  <div v-if="isVisible" ref="modalOverlay" class="modal-overlay" @click.self="handleOverlayClick">
    <div class="modal-wrapper">
      <div ref="closeButtonWrapper" class="close-btn-wrapper">
        <IconButton ref="closeButton" icon="close" variant="rounded" size="large"
          :aria-label="t('common.close')" @click="close" />
      </div>

      <div ref="modalShell" class="modal-shell">
        <!-- Clip carries the animated (spring) height; masks the scroller. -->
        <div ref="modalClip" class="modal-clip">
          <!-- Scroller has an explicit px height (= final target, never animated). -->
          <div ref="modalScroller" class="modal-scroller">
            <div ref="contentInner" class="modal-content-inner">
              <slot></slot>
            </div>
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
import { useTimer } from '@/composables/useTimer';
import { useI18n } from '@/services/i18n';

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

const { t } = useI18n();

const modalShell = ref(null);
const modalClip = ref(null);
const modalScroller = ref(null);
const modalOverlay = ref(null);
const closeButton = ref(null);
const closeButtonWrapper = ref(null);
const contentInner = ref(null);

// Animated height: observe contentInner, write clip + scroller through one writer.
const { setTargetHeight, resetFirstResize, endFirstResize, requestHeightDelta, springClipDelta } = useAnimatedHeight(contentInner, {
  clipRef: modalClip,
  scrollerRef: modalScroller,
  threshold: 2,
  skipFirstResize: true,
  getExtraHeight: () => {
    if (!modalScroller.value) return 0;
    const style = getComputedStyle(modalScroller.value);
    return parseFloat(style.paddingTop) + parseFloat(style.paddingBottom);
  },
  getMaxHeight: () => {
    if (!modalOverlay.value) return Infinity;
    const style = getComputedStyle(modalOverlay.value);
    const paddingTop = parseFloat(style.paddingTop);
    const paddingBottom = parseFloat(style.paddingBottom);
    // Cosmetic breathing room at the viewport edge; no longer load-bearing for
    // scroll correctness (the scroller's explicit height keeps maxScroll exact).
    const bounceMargin = 24;
    return modalOverlay.value.clientHeight - paddingTop - paddingBottom - bounceMargin;
  }
});

const isVisible = ref(false);
const isAnimating = ref(false);

// The scroller is the single scroll container + the scrollElRef for navigation.
provide('modalContentRef', modalScroller);

// Accordions (ToggleSection / Network) and multiroom zones on EXPAND pre-announce
// intra-view height changes through this so the clip springs in lock-step.
provide('modalRequestHeightDelta', requestHeightDelta);

// Multiroom zone COLLAPSE: the item eases its own height to 0 on the same spring curve
// while this springs the clip to the collapsed height (a native spring → bounce) and
// keeps the scroller matched to the reflow. Frame and content stay in sync; the frame's
// end bounce dips into the scroller's bottom padding. Correct wherever the zone sits.
provide('modalSpringCollapse', springClipDelta);

// contentInner ref so children can measure exact height deltas.
provide('modalContentInnerRef', contentInner);

// Navigation height writer for useViewTransition: measures the live stacked
// content, writes scroller height + reflow, runs the scrollTop restore (beforeClip),
// then springs the clip — the mandated order height → reflow → scrollTop → clip.
provide('modalSetNavHeight', (beforeClip) => setTargetHeight(null, { beforeClip }));

// Absolute height writer for accordions that measure AFTER mutating the DOM
// (NetworkSettings WiFi-card corrections): they hold the settled height, so they
// set it directly instead of a delta (a delta would double-count the live content).
provide('modalSetContentHeight', () => setTargetHeight());

// Variables to cancel ongoing timeouts
const timer = useTimer();
let animationTimeouts = [];
let inactivityTimer = null;

// A tap inside the modal means the next height change is user-driven, not late-settling
// content, so it ends the open-height snap window — the hook for controls that only
// reflow the content (multiroom toggle) instead of pre-announcing a delta.
let releaseSnapWindow = null;

function armSnapWindowRelease() {
  disarmSnapWindowRelease();
  if (!modalOverlay.value) return;
  releaseSnapWindow = () => {
    endFirstResize();
    disarmSnapWindowRelease();
  };
  // Capture: fires even if a control stops propagation on its own pointerdown.
  modalOverlay.value.addEventListener('pointerdown', releaseSnapWindow, { capture: true, passive: true });
}

function disarmSnapWindowRelease() {
  if (!releaseSnapWindow) return;
  modalOverlay.value?.removeEventListener('pointerdown', releaseSnapWindow, { capture: true });
  releaseSnapWindow = null;
}

function clearAllTimeouts() {
  animationTimeouts.forEach(timeout => timer.clear(timeout));
  animationTimeouts = [];
}

function clearInactivityTimer() {
  if (inactivityTimer) {
    timer.clear(inactivityTimer);
    inactivityTimer = null;
  }
}

function resetInactivityTimer() {
  clearInactivityTimer();

  // Start a new 120-second timer
  inactivityTimer = timer.setTimeout(() => {
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

  if (!modalShell.value || !modalOverlay.value || !closeButtonWrapper.value) return;

  // Initial overlay state (invisible)
  modalOverlay.value.style.transition = 'none';
  modalOverlay.value.style.opacity = '0';

  // Initial shell state (invisible and scaled down)
  modalShell.value.style.transition = 'none';
  modalShell.value.style.opacity = '0';
  modalShell.value.style.transform = 'scale(0.95)';

  // Initial close button state (invisible and higher position)
  closeButtonWrapper.value.style.transition = 'none';
  closeButtonWrapper.value.classList.remove('visible');
  closeButton.value.$el.style.transition = 'none';
  closeButton.value.$el.style.opacity = '0';

  // Force reflow
  modalShell.value.offsetHeight;

  armSnapWindowRelease();

  // Overlay enter animation (immediate)
  const overlayTimeout = timer.setTimeout(() => {
    if (!modalOverlay.value) return;
    modalOverlay.value.style.transition = `opacity ${ANIMATION_TIMINGS.overlayDuration}ms var(--easeOutCubic)`;
    modalOverlay.value.style.opacity = '1';
  }, ANIMATION_TIMINGS.overlayDelay);
  animationTimeouts.push(overlayTimeout);

  // Shell enter animation (scale via --transition-spring, opacity via ease-out).
  // Height is owned by the clip (its own CSS spring), never by the shell.
  const containerTimeout = timer.setTimeout(() => {
    if (!modalShell.value) return;
    modalShell.value.style.transition = 'transform var(--transition-spring-snappy), opacity 300ms var(--easeOutCubic)';
    modalShell.value.style.opacity = '1';
    modalShell.value.style.transform = 'scale(1)';
  }, ANIMATION_TIMINGS.containerDelay);
  animationTimeouts.push(containerTimeout);

  // Delayed close button animation (wrapper slides, button fades independently)
  const closeButtonTimeout = timer.setTimeout(() => {
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

  const finalTimeout = timer.setTimeout(() => {
    isAnimating.value = false;
    endFirstResize();
    disarmSnapWindowRelease();
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
  disarmSnapWindowRelease();

  isAnimating.value = true;

  if (!modalShell.value || !modalOverlay.value || !closeButtonWrapper.value) return;

  // Exit animation with ease-out for closing
  const overlayCloseTimeout = timer.setTimeout(() => {
    if (!modalOverlay.value) return;
    modalOverlay.value.style.transition = `opacity ${ANIMATION_TIMINGS.closeOverlayDuration}ms var(--easeOutCubic)`;
    modalOverlay.value.style.opacity = '0';
  }, ANIMATION_TIMINGS.closeOverlayDelay);
  animationTimeouts.push(overlayCloseTimeout);

  const containerCloseTimeout = timer.setTimeout(() => {
    if (!modalShell.value) return;
    modalShell.value.style.transition = `transform ${ANIMATION_TIMINGS.closeContainerDuration}ms var(--easeOutCubic), opacity ${ANIMATION_TIMINGS.closeContainerDuration}ms var(--easeOutCubic)`;
    modalShell.value.style.opacity = '0';
    modalShell.value.style.transform = 'scale(0.98)';
  }, ANIMATION_TIMINGS.closeContainerDelay);
  animationTimeouts.push(containerCloseTimeout);

  const closeButtonCloseTimeout = timer.setTimeout(() => {
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

  const finalCloseTimeout = timer.setTimeout(() => {
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

function removeActivityListeners() {
  if (!modalOverlay.value) return;

  modalOverlay.value.removeEventListener('pointerdown', handleUserActivity);
  modalOverlay.value.removeEventListener('wheel', handleUserActivity);
  modalOverlay.value.removeEventListener('touchstart', handleUserActivity);
}

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
  // Pending animation timeouts and the inactivity timer are auto-cleared by useTimer.
  removeActivityListeners();
  disarmSnapWindowRelease();
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

/* Shell: chrome (radius, glass stroke, open/close scale+opacity). Does NOT scroll
   and does NOT carry the animated height — it wraps the clip and tracks its height. */
.modal-shell {
  position: relative;
  background: var(--color-background-neutral-50);
  border-radius: var(--radius-08);
  width: 100%;
  max-height: 100%;
  display: flex;
  flex-direction: column;
  opacity: 0;
  overflow: hidden;
}

.modal-shell::before {
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

/* Clip: carries the animated (spring) height and masks the scroller beneath it.
   May overshoot harmlessly — it only over-reveals/over-masks a few px. */
.modal-clip {
  flex: 0 0 auto;
  max-height: 100%;
  overflow: hidden;
  transition: height var(--transition-spring-light);
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

/* Scroller: the single scroll container. Its height is set explicitly in px
   (= final target, transition:none) so clientHeight is independent of the clip
   spring — that is what keeps maxScroll exact and scrollTop writes un-clamped.
   overflow-y is permanently auto; it is never toggled. */
.modal-scroller {
  overflow-y: auto;
  padding: var(--space-04);
  display: flex;
  flex-direction: column;
  touch-action: pan-y;
  border-radius: var(--radius-08);
  transition: none;
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

  .modal-shell,
  .modal-scroller,
  .modal-shell::before {
    border-radius: var(--radius-07);
  }

}
</style>