import { ref, onMounted, onUnmounted } from 'vue';

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

  // Plain variables (not reactive — only used inside event handlers)
  let dragStartY = 0;
  let dragCurrentY = 0;
  let dragStartTime = 0;
  let dragActionTaken = false;
  let dragGraceTimeout = null;

  // Additional-apps drag state
  let isDraggingAdditional = false;
  let additionalDragStartY = 0;
  let additionalDragMoved = false;

  // Inline reference to the touchmove-prevent handler so we can remove it
  const preventTouchMove = (e) => e.preventDefault();

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

    if (dragGraceTimeout) {
      clearTimeout(dragGraceTimeout);
      dragGraceTimeout = null;
    }

    // Only reset gesture state if volume hold is not tracking a pointer
    // (volume hold sets gestureStartPosition on pointerdown)
    if (gestureStartPosition.value.x === 0 && gestureStartPosition.value.y === 0) {
      resetGestureState();
    }
  };

  const onDragMove = (e) => {
    // Handle additional-apps drag first
    if (isDraggingAdditional) {
      const deltaY = getEventY(e) - additionalDragStartY;
      if (Math.abs(deltaY) > 5) {
        additionalDragMoved = true;
        e.preventDefault();
      }
      if (Math.abs(deltaY) >= 20 && deltaY > 0) {
        e.preventDefault();
        onCloseAdditionalApps();
        isDraggingAdditional = false;
        additionalDragMoved = false;
      }
      return;
    }

    if (!isDragging.value) return;

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

      if (deltaY > 0 && !isVisible.value) {
        onShow();
      } else if (deltaY < 0 && isVisible.value) {
        onHide();
      }

      dragGraceTimeout = setTimeout(() => {
        isDragging.value = false;
        dragActionTaken = false;
        resetGestureState();
      }, 200);
    }
  };

  const onDragEnd = () => {
    if (isDraggingAdditional) {
      isDraggingAdditional = false;
      additionalDragMoved = false;
      return;
    }

    if (!dragActionTaken) {
      isDragging.value = false;
      resetGestureState();
    }

    onResetHideTimer();
  };

  // === Click-outside ===
  const onClickOutside = (event) => {
    if (!isVisible.value ||
      (dockContainer.value && dockContainer.value.contains(event.target)) ||
      event.target.closest('.modal-overlay, .modal-container, .modal-content')) {
      return;
    }
    onHide();
  };

  // === Lifecycle ===
  const setupDragEvents = () => {
    const zone = dragZone.value;
    const dockEl = dock.value;
    if (!zone) return;

    zone.addEventListener('mousedown', onDragStart);
    zone.addEventListener('touchstart', onDragStart, { passive: false });
    zone.addEventListener('touchmove', preventTouchMove, { passive: false });

    if (dockEl) {
      dockEl.addEventListener('mousedown', onDragStart);
      dockEl.addEventListener('touchstart', onDragStart, { passive: false });
    }

    document.addEventListener('mousemove', onDragMove);
    document.addEventListener('mouseup', onDragEnd);
    document.addEventListener('touchmove', onDragMove, { passive: false });
    document.addEventListener('touchend', onDragEnd);
    document.addEventListener('click', onClickOutside);
    document.addEventListener('pointerup', onVolumeHoldEnd);
    document.addEventListener('pointercancel', onVolumeHoldEnd);
  };

  const removeDragEvents = () => {
    const zone = dragZone.value;
    const dockEl = dock.value;

    if (zone) {
      zone.removeEventListener('mousedown', onDragStart);
      zone.removeEventListener('touchstart', onDragStart);
      zone.removeEventListener('touchmove', preventTouchMove);
    }
    if (dockEl) {
      dockEl.removeEventListener('mousedown', onDragStart);
      dockEl.removeEventListener('touchstart', onDragStart);
    }

    removeAdditionalDragEvents();

    document.removeEventListener('mousemove', onDragMove);
    document.removeEventListener('mouseup', onDragEnd);
    document.removeEventListener('touchmove', onDragMove);
    document.removeEventListener('touchend', onDragEnd);
    document.removeEventListener('click', onClickOutside);
    document.removeEventListener('pointerup', onVolumeHoldEnd);
    document.removeEventListener('pointercancel', onVolumeHoldEnd);
  };

  const cleanup = () => {
    removeDragEvents();
    if (dragGraceTimeout) {
      clearTimeout(dragGraceTimeout);
      dragGraceTimeout = null;
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
