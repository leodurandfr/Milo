import { ref, onUnmounted } from 'vue';
import { useTimer } from '@/composables/useTimer';

/**
 * Dock drag/gesture composable — handles all pointer events for:
 * - Main dock show/hide via vertical swipe (velocity-adaptive threshold)
 * - Additional-apps panel swipe-to-close
 * - Click-outside detection
 * - Gesture movement tracking (shared with volume hold)
 *
 * @param {Object} options
 * @param {import('vue').Ref<HTMLElement>} options.dragZone - drag zone template ref
 * @param {import('vue').Ref<HTMLElement>} options.dock - dock inner element ref
 * @param {import('vue').Ref<HTMLElement>} options.dockContainer - dock container ref (for click-outside)
 * @param {import('vue').Ref<HTMLElement>} options.additionalAppsContainer - additional apps panel ref
 * @param {import('vue').Ref<boolean>} options.isVisible - dock visibility (read by click-outside guard)
 * @param {import('vue').Ref<boolean>} options.showAdditionalApps - additional panel visibility
 * @param {Function} options.onShow - called when swipe-up opens the dock
 * @param {Function} options.onHide - called when swipe-down closes the dock
 * @param {Function} options.onCloseAdditionalApps - called when additional panel swiped down
 * @param {Function} options.onVolumeHoldEnd - called when movement exceeds threshold
 * @param {Function} options.onResetHideTimer - called after drag-end to restart auto-hide
 */
export function useDockDrag({
  dragZone,
  dock,
  dockContainer,
  additionalAppsContainer,
  isVisible,
  showAdditionalApps,
  onShow,
  onHide,
  onCloseAdditionalApps,
  onVolumeHoldEnd,
  onResetHideTimer,
}) {
  const MOVE_THRESHOLD = 10;

  // Reactive state (used in template bindings)
  const isDragging = ref(false);
  const gestureHasMoved = ref(false);
  const gestureStartPosition = ref({ x: 0, y: 0 });
  const timer = useTimer();

  // Plain variables (not reactive — only used inside event handlers)
  let dragStartY = 0;
  let dragCurrentY = 0;
  let dragStartTime = 0;
  let dragActionTaken = false;
  let dragGraceTimeout = null;
  let startedInBand = false;
  let suppressNextClick = false;
  let suppressClickTimeout = null;

  // Additional-apps drag state
  let isDraggingAdditional = false;
  let additionalDragStartY = 0;
  let additionalDragMoved = false;

  // === Event coordinate helpers ===
  const getEventY = (e) => e.type.includes('touch') || e.pointerType === 'touch'
    ? (e.touches?.[0]?.clientY || e.changedTouches?.[0]?.clientY || e.clientY) : e.clientY;

  const getEventX = (e) => e.type.includes('touch') || e.pointerType === 'touch'
    ? (e.touches?.[0]?.clientX || e.changedTouches?.[0]?.clientX || e.clientX) : e.clientX;

  // === Gesture state ===
  const resetGestureState = () => {
    gestureHasMoved.value = false;
    gestureStartPosition.value = { x: 0, y: 0 };
  };

  // === Additional-apps drag ===
  const onAdditionalDragStart = (e) => {
    if (!showAdditionalApps.value) return;
    isDraggingAdditional = true;
    additionalDragMoved = false;
    additionalDragStartY = getEventY(e);
  };

  const setupAdditionalDragEvents = () => {
    const el = additionalAppsContainer.value;
    if (el) {
      el.addEventListener('mousedown', onAdditionalDragStart);
      el.addEventListener('touchstart', onAdditionalDragStart, { passive: false });
    }
  };

  const removeAdditionalDragEvents = () => {
    const el = additionalAppsContainer.value;
    if (el) {
      el.removeEventListener('mousedown', onAdditionalDragStart);
      el.removeEventListener('touchstart', onAdditionalDragStart);
    }
  };

  // === Main drag handlers ===
  const onDragStart = (e) => {
    isDragging.value = true;
    dragStartY = getEventY(e);
    dragCurrentY = dragStartY;
    dragStartTime = Date.now();
    dragActionTaken = false;
    startedInBand = false;
    suppressNextClick = false;

    if (dragGraceTimeout) {
      timer.clear(dragGraceTimeout);
      dragGraceTimeout = null;
    }

    // Only reset gesture state if volume hold is not tracking a pointer
    // (volume hold sets gestureStartPosition on pointerdown)
    if (gestureStartPosition.value.x === 0 && gestureStartPosition.value.y === 0) {
      resetGestureState();
    }
  };

  // Did the gesture start inside the bottom drag band? The band's geometry is
  // defined in CSS (.drag-zone, responsive) — we read its rect rather than
  // duplicate the sizing here. The element stays pointer-events:none, used only
  // as a coordinate marker, so it never intercepts taps.
  const pointInBand = (e) => {
    const zone = dragZone.value;
    if (!zone) return false;
    const rect = zone.getBoundingClientRect();
    const x = getEventX(e);
    const y = getEventY(e);
    return x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom;
  };

  // Document-level gesture start. Nothing is captured here (no preventDefault /
  // stopPropagation), so a plain tap flows through to whatever is underneath.
  const onDocumentDragStart = (e) => {
    // Document-level detection sees through z-index stacking, so ignore gestures
    // over an open modal (z 5000, above the dock) — otherwise a swipe inside one
    // would open the dock behind it and hijack the modal's own scroll. Mirrors
    // the guard in onClickOutside. Lyrics is a full-screen slot (not a modal)
    // that repurposes the same swipe-up/down gesture for its own playback bar,
    // so it gets the same exclusion.
    if (e.target.closest('.modal-overlay, .modal-shell, .modal-scroller, .lyrics-view')) return;

    if (isVisible.value) {
      // Dock open: allow a swipe-down-to-close begun on the dock itself (not the
      // overflow panel, which runs its own gesture stream).
      if (dock.value && dock.value.contains(e.target)) {
        onDragStart(e);
      }
      return;
    }
    // Dock hidden: only a gesture starting in the bottom band can open it.
    if (pointInBand(e)) {
      onDragStart(e);
      startedInBand = true;
    }
  };

  const onDragMove = (e) => {
    // Handle additional-apps drag first
    if (isDraggingAdditional) {
      const deltaY = getEventY(e) - additionalDragStartY;
      if (Math.abs(deltaY) > 5) {
        // The overflow panel uses its own gesture stream (it doesn't touch the
        // shared gestureHasMoved), so cancel any pending app-hold explicitly on
        // the first swipe to avoid a misfired close.
        if (!additionalDragMoved) onVolumeHoldEnd();
        additionalDragMoved = true;
        e.preventDefault();
      }
      if (Math.abs(deltaY) >= 20 && deltaY > 0) {
        e.preventDefault();
        onCloseAdditionalApps();
        isDraggingAdditional = false;
      }
      return;
    }

    if (!isDragging.value) return;

    if (startedInBand) e.preventDefault();

    const currentY = getEventY(e);
    const currentX = getEventX(e);

    // Check movement to cancel volume hold
    if (!gestureHasMoved.value) {
      const deltaX = Math.abs(currentX - gestureStartPosition.value.x);
      const deltaY = Math.abs(currentY - gestureStartPosition.value.y);

      if (deltaX > MOVE_THRESHOLD || deltaY > MOVE_THRESHOLD) {
        gestureHasMoved.value = true;
        onVolumeHoldEnd();
      }
    }

    // Velocity-adaptive drag threshold
    dragCurrentY = currentY;
    const deltaY = dragStartY - dragCurrentY;
    const dragDuration = Date.now() - dragStartTime;
    const velocity = Math.abs(deltaY) / Math.max(dragDuration, 1);

    let threshold = 30;
    if (velocity >= 0.5) {
      threshold = Math.max(20, 30 - (velocity * 10));
    }

    if (Math.abs(deltaY) >= threshold && !dragActionTaken) {
      dragActionTaken = true;

      suppressNextClick = true;
      if (suppressClickTimeout) timer.clear(suppressClickTimeout);
      suppressClickTimeout = timer.setTimeout(() => { suppressNextClick = false; }, 400);

      if (deltaY > 0 && !isVisible.value) {
        onShow();
      } else if (deltaY < 0 && isVisible.value) {
        onHide();
      }

      dragGraceTimeout = timer.setTimeout(() => {
        isDragging.value = false;
        dragActionTaken = false;
        resetGestureState();
      }, 200);
    }
  };

  const onDragEnd = () => {
    if (isDraggingAdditional) {
      isDraggingAdditional = false;
      return;
    }

    if (!dragActionTaken) {
      isDragging.value = false;
      resetGestureState();
    }

    onResetHideTimer();
  };

  const onDocumentClickCapture = (event) => {
    if (!suppressNextClick) return;
    suppressNextClick = false;
    event.preventDefault();
    event.stopPropagation();
  };

  // === Click-outside ===
  const onClickOutside = (event) => {
    if (!isVisible.value ||
      (dockContainer.value && dockContainer.value.contains(event.target)) ||
      event.target.closest('.modal-overlay, .modal-shell, .modal-scroller, .lyrics-view')) {
      return;
    }
    onHide();
  };

  // === Lifecycle ===
  const setupDragEvents = () => {
    // Gesture start is detected at the document level and gated by region
    // (bottom band when hidden, dock element when visible). The .drag-zone
    // element is pointer-events:none and captures nothing, so taps fall through
    // to the content beneath it.
    document.addEventListener('mousedown', onDocumentDragStart);
    document.addEventListener('touchstart', onDocumentDragStart, { passive: true });

    document.addEventListener('mousemove', onDragMove);
    document.addEventListener('mouseup', onDragEnd);
    document.addEventListener('touchmove', onDragMove, { passive: false });
    document.addEventListener('touchend', onDragEnd);
    document.addEventListener('click', onDocumentClickCapture, true);
    document.addEventListener('click', onClickOutside);
    document.addEventListener('pointerup', onVolumeHoldEnd);
    document.addEventListener('pointercancel', onVolumeHoldEnd);
  };

  const removeDragEvents = () => {
    removeAdditionalDragEvents();

    document.removeEventListener('mousedown', onDocumentDragStart);
    document.removeEventListener('touchstart', onDocumentDragStart);
    document.removeEventListener('mousemove', onDragMove);
    document.removeEventListener('mouseup', onDragEnd);
    document.removeEventListener('touchmove', onDragMove);
    document.removeEventListener('touchend', onDragEnd);
    document.removeEventListener('click', onDocumentClickCapture, true);
    document.removeEventListener('click', onClickOutside);
    document.removeEventListener('pointerup', onVolumeHoldEnd);
    document.removeEventListener('pointercancel', onVolumeHoldEnd);
  };

  const cleanup = () => {
    removeDragEvents();
    if (dragGraceTimeout) {
      timer.clear(dragGraceTimeout);
      dragGraceTimeout = null;
    }
    if (suppressClickTimeout) {
      timer.clear(suppressClickTimeout);
      suppressClickTimeout = null;
    }
    resetGestureState();
  };

  onUnmounted(cleanup);

  return {
    // Reactive state for template bindings
    isDragging,
    gestureHasMoved,
    gestureStartPosition,

    // Additional-apps drag state
    get additionalDragMoved() { return additionalDragMoved; },
    resetAdditionalDragMoved() { additionalDragMoved = false; },

    // Event coordinate helpers (shared with volume hold)
    getEventX,
    getEventY,

    // Gesture state
    resetGestureState,

    // Additional-apps drag lifecycle
    setupAdditionalDragEvents,
    removeAdditionalDragEvents,

    // Main lifecycle
    setupDragEvents,
    removeDragEvents,
    cleanup,
  };
}
