import { onUnmounted } from 'vue';
import { useTimer } from '@/composables/useTimer';

/**
 * Hold-to-close composable for dock source buttons.
 *
 * Holding (pointer down beyond HOLD_DELAY without movement) a dock icon that
 * represents the currently active audio source fires onCloseActive() to
 * deactivate it (source → 'none'), as if closing the active app. A single
 * `holdFired` flag lets the trailing click be swallowed so the source isn't
 * immediately reselected on release. A quick tap never fires the hold — the
 * button's normal @click keeps working.
 *
 * Reuses the shared gesture refs owned by useDockDrag (gestureHasMoved /
 * gestureStartPosition) so a dock swipe/scroll aborts the hold, mirroring
 * useVolumeHold. The hold timer itself is cleared via onAppHoldEnd, which the
 * dock routes through the drag's movement/release/cancel callback.
 *
 * @param {Object} options
 * @param {Function} options.isActiveSource - (appId: string) => boolean
 * @param {Function} options.onCloseActive - () => void — called when a hold lands on the active source
 * @param {import('vue').Ref<boolean>} options.gestureHasMoved - shared ref from drag composable
 * @param {import('vue').Ref<{x:number,y:number}>} options.gestureStartPosition - shared ref
 * @param {Function} options.getEventX - normalize event → clientX
 * @param {Function} options.getEventY - normalize event → clientY
 */
export function useDockAppHold({
  isActiveSource,
  onCloseActive,
  gestureHasMoved,
  gestureStartPosition,
  getEventX,
  getEventY,
}) {
  const HOLD_DELAY = 500;

  const timer = useTimer();
  let holdTimer = null;
  let holdFired = false;
  let lockedPointerType = null;

  const onAppHoldStart = (appId, event) => {
    // Ignore conflicting pointer types (e.g. ghost mouse after touch)
    if (lockedPointerType && lockedPointerType !== event.pointerType) return;

    lockedPointerType = event.pointerType;
    // Seed the shared start point so onDragMove measures travel from the real
    // press point (leaving it at {0,0} would cancel the hold on the first move).
    gestureStartPosition.value = { x: getEventX(event), y: getEventY(event) };
    gestureHasMoved.value = false;
    holdFired = false;

    holdTimer = timer.setTimeout(() => {
      if (!gestureHasMoved.value
        && lockedPointerType === event.pointerType
        && isActiveSource(appId)) {
        holdFired = true;
        onCloseActive();
      }
    }, HOLD_DELAY);
  };

  const onAppHoldEnd = (event) => {
    if (event && lockedPointerType && event.pointerType !== lockedPointerType) return;

    lockedPointerType = null;
    if (holdTimer) {
      timer.clear(holdTimer);
      holdTimer = null;
    }
    // NB: holdFired is intentionally NOT reset here — the trailing click reads
    // it via consumeHoldFired(). It is reset on the next onAppHoldStart.

    // Clear shared gesture state (mirrors useVolumeHold) so the next gesture's
    // onDragStart resets cleanly.
    gestureStartPosition.value = { x: 0, y: 0 };
    gestureHasMoved.value = false;
  };

  // Read-and-clear the hold-fired flag; returns true if a hold just closed the
  // active source (so the click handler should swallow the trailing click).
  const consumeHoldFired = () => {
    const fired = holdFired;
    holdFired = false;
    return fired;
  };

  const cleanup = () => {
    onAppHoldEnd();
  };

  onUnmounted(cleanup);

  return { onAppHoldStart, onAppHoldEnd, consumeHoldFired, cleanup };
}
