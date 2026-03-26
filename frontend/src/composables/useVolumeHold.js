import { onUnmounted } from 'vue';

/**
 * Hold-to-repeat composable for discrete volume buttons.
 *
 * Distinguishes taps (single adjustVolume on pointerup) from holds
 * (first adjustVolume after initialDelay, then repeating every repeatInterval).
 * Movement beyond MOVE_THRESHOLD cancels the hold to avoid conflicts
 * with drag gestures sharing the same pointer stream.
 *
 * @param {Object} options
 * @param {Function} options.adjustVolume - (delta: number) => void
 * @param {Function} [options.onHoldStart] - (delta, intervalMs) => void — called when hold begins
 * @param {Function} [options.onHoldEnd] - () => void — called when hold ends
 * @param {import('vue').Ref<boolean>} options.gestureHasMoved - shared ref from drag composable
 * @param {import('vue').Ref<{x:number,y:number}>} options.gestureStartPosition - shared ref
 * @param {Function} options.getEventX - normalize event → clientX
 * @param {Function} options.getEventY - normalize event → clientY
 */
export function useVolumeHold({
  adjustVolume,
  onHoldStart,
  onHoldEnd,
  gestureHasMoved,
  gestureStartPosition,
  getEventX,
  getEventY,
}) {
  const INITIAL_DELAY = 400;
  const REPEAT_INTERVAL = 50;

  let startTimer = null;
  let repeatTimer = null;
  let isHolding = false;
  let currentDelta = 0;
  let actionTaken = false;
  let lockedPointerType = null;

  const onVolumeHoldStart = (delta, event) => {
    // Ignore conflicting pointer types (e.g. ghost mouse after touch)
    if (lockedPointerType && lockedPointerType !== event.pointerType) return;

    lockedPointerType = event.pointerType;
    gestureStartPosition.value = { x: getEventX(event), y: getEventY(event) };
    gestureHasMoved.value = false;
    currentDelta = delta;
    actionTaken = false;

    startTimer = setTimeout(() => {
      if (!gestureHasMoved.value && lockedPointerType === event.pointerType) {
        adjustVolume(delta);
        actionTaken = true;
        isHolding = true;
        onHoldStart?.(delta, REPEAT_INTERVAL);

        repeatTimer = setInterval(() => {
          if (isHolding) {
            adjustVolume(currentDelta);
          } else {
            clearInterval(repeatTimer);
          }
        }, REPEAT_INTERVAL);
      }
    }, INITIAL_DELAY);
  };

  const onVolumeHoldEnd = (event) => {
    if (event && lockedPointerType && event.pointerType !== lockedPointerType) return;

    // Tap: fire once on release if no hold action was taken and no movement
    if (!gestureHasMoved.value && !actionTaken && currentDelta !== 0) {
      adjustVolume(currentDelta);
    }

    isHolding = false;
    lockedPointerType = null;
    onHoldEnd?.();

    if (startTimer) {
      clearTimeout(startTimer);
      startTimer = null;
    }
    if (repeatTimer) {
      clearInterval(repeatTimer);
      repeatTimer = null;
    }

    currentDelta = 0;
    actionTaken = false;

    // Clear shared gesture state so useDockDrag's onDragStart
    // correctly resets on the next gesture (mirrors original
    // volumePointerType = null behavior)
    gestureStartPosition.value = { x: 0, y: 0 };
    gestureHasMoved.value = false;
  };

  const cleanup = () => {
    onVolumeHoldEnd();
  };

  onUnmounted(cleanup);

  return { onVolumeHoldStart, onVolumeHoldEnd, cleanup };
}
