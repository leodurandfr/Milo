// frontend/src/composables/usePullToDismiss.js
// The phone's pull down on a full-screen view: the view follows the finger down,
// and on release either slides out (onDismiss) or springs back. Only a move
// confirmed downward and vertical is taken; a tap or a horizontal swipe passes
// through untouched. What the pull looks like is the caller's: this holds where
// the finger is (`offset`, `progress` over the view's height), whether a pull is
// under way at all (`active`), and the curve a release settles on (`timing`,
// null while the finger moves the view itself).
import { computed, ref } from 'vue';
import { useTimer } from '@/composables/useTimer';

const LOCK_PX = 10;
const DISMISS_FRACTION = 0.25;
const FLICK_PX = 40;
const FLICK_PX_PER_MS = 0.5;
const SETTLE_MS = 300;
// A browser still fires a click after a drag that ended on a button.
const CLICK_GUARD_MS = 400;

/**
 * What a released pull does: past a quarter of the view, or a quick flick
 * past FLICK_PX, it dismisses; anything else springs back.
 *
 * @param {{ dy: number, durationMs: number, height: number }} pull
 * @returns {'dismiss' | 'cancel'}
 */
export function pullOutcome({ dy, durationMs, height }) {
  if (dy <= 0) return 'cancel';
  if (dy > height * DISMISS_FRACTION) return 'dismiss';
  if (dy > FLICK_PX && dy / Math.max(durationMs, 1) > FLICK_PX_PER_MS) return 'dismiss';
  return 'cancel';
}

/**
 * @param {{ enabled: import('vue').Ref<boolean>, onDismiss: () => void }} options
 */
export function usePullToDismiss({ enabled, onDismiss }) {
  const timer = useTimer();
  const offset = ref(0);
  const dragging = ref(false);
  const settling = ref(false);
  const height = ref(1);

  let tracking = false;
  let startX = 0;
  let startY = 0;
  let startTime = 0;
  let endedAt = 0;

  function onTouchStart(e) {
    if (!enabled.value || settling.value) return;
    const touch = e.touches[0];
    startX = touch.clientX;
    startY = touch.clientY;
    startTime = Date.now();
    height.value = e.currentTarget.offsetHeight || 1;
    tracking = true;
  }

  function onTouchMove(e) {
    if (!tracking) return;
    const touch = e.touches[0];
    const dx = touch.clientX - startX;
    const dy = touch.clientY - startY;
    if (!dragging.value) {
      if (dy > LOCK_PX && dy > Math.abs(dx)) {
        dragging.value = true;
      } else {
        if (Math.abs(dx) > LOCK_PX || dy < -LOCK_PX) tracking = false;
        return;
      }
    }
    if (e.cancelable) e.preventDefault();
    offset.value = Math.max(0, dy);
  }

  function onTouchEnd(e) {
    if (!tracking) return;
    tracking = false;
    if (!dragging.value) return;
    dragging.value = false;
    endedAt = Date.now();
    const touch = e.changedTouches[0];
    const outcome = pullOutcome({
      dy: touch ? touch.clientY - startY : offset.value,
      durationMs: endedAt - startTime,
      height: height.value
    });
    settling.value = true;
    offset.value = outcome === 'dismiss' ? height.value : 0;
    timer.setTimeout(() => {
      // A dismissed view stays where it slid to: it leaves from off-screen.
      if (outcome === 'dismiss') {
        onDismiss();
        return;
      }
      settling.value = false;
    }, SETTLE_MS);
  }

  function onClickCapture(e) {
    if (Date.now() - endedAt < CLICK_GUARD_MS) {
      e.stopPropagation();
      e.preventDefault();
    }
  }

  return {
    offset,
    progress: computed(() => Math.min(1, offset.value / height.value)),
    active: computed(() => dragging.value || settling.value),
    timing: computed(() => (settling.value ? `${SETTLE_MS}ms var(--easeInOutCubic)` : null)),
    onTouchStart,
    onTouchMove,
    onTouchEnd,
    onClickCapture
  };
}
