// frontend/src/composables/useSwipeVisibility.js
// Bottom-edge swipe-up-to-show / swipe-down-to-hide gesture for a single panel.
// A smaller sibling of useDockDrag's vertical-swipe logic, without any of the
// Dock-specific concerns (additional apps, volume hold, app hold) — used by the
// Lyrics playback bar, which repurposes the same gesture the Dock normally owns
// (useDockDrag ignores gestures over .lyrics-view for exactly this reason).
import { onMounted, onUnmounted } from 'vue';
import { markTouchRegion, holdScroll } from '@/utils/touchScroll';

// `handle` is an optional always-visible grip (the Lyrics bar's arrow) that
// stays outside the panel so it survives the bar being hidden — it has to be
// accepted explicitly in both directions, otherwise the element that looks like
// the bar's drag handle is the one spot the gesture ignores. `hideZone` is the
// one region, besides the handle, where a gesture can hide the shown panel —
// the rest of the panel may let touches through to the content it covers.
export function useSwipeVisibility({ dragZone, hideZone, handle, isVisible, onShow, onHide }) {
  let dragging = false;
  let startedInBand = false;
  let startY = 0;
  let startTime = 0;
  let actionTaken = false;
  let unmarkBand = null;
  let unmarkHandle = null;

  const getEventY = (e) => e.type.includes('touch')
    ? (e.touches?.[0]?.clientY ?? e.changedTouches?.[0]?.clientY ?? e.clientY) : e.clientY;
  const getEventX = (e) => e.type.includes('touch')
    ? (e.touches?.[0]?.clientX ?? e.changedTouches?.[0]?.clientX ?? e.clientX) : e.clientX;

  // Did the gesture start inside the bottom reveal band? Mirrors useDockDrag's
  // pointInBand, over the same shared .swipe-reveal-band geometry — the zone
  // stays pointer-events:none, so it never intercepts a tap; a touch starting
  // in it, bar hidden, has its scroll held until the finger lifts (holdScroll).
  const pointInBand = (e) => {
    const zone = dragZone.value;
    if (!zone) return false;
    const rect = zone.getBoundingClientRect();
    const x = getEventX(e);
    const y = getEventY(e);
    return x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom;
  };

  const onHandle = (e) => !!handle?.value?.contains(e.target);

  const onDragStart = (e) => {
    if (isVisible.value) {
      // Bar shown: only a gesture starting in the hide zone, or on the handle
      // (which rests above it, in the bar's top band), can hide it.
      if (!hideZone.value?.contains(e.target) && !onHandle(e)) return;
      startedInBand = false;
    } else {
      // Bar hidden: only a gesture starting in the bottom band, or on the
      // handle (which the band may not fully cover on a short screen), can
      // reveal it.
      if (!pointInBand(e) && !onHandle(e)) return;
      startedInBand = true;
      if (e.type === 'touchstart') holdScroll();
    }
    dragging = true;
    startY = getEventY(e);
    startTime = Date.now();
    actionTaken = false;
  };

  const onDragMove = (e) => {
    if (!dragging) return;
    // A touch is held by holdScroll() from its touchstart.
    if (startedInBand && e.type === 'mousemove') e.preventDefault();

    const deltaY = startY - getEventY(e);
    const duration = Date.now() - startTime;
    const velocity = Math.abs(deltaY) / Math.max(duration, 1);
    const threshold = velocity >= 0.5 ? Math.max(20, 30 - velocity * 10) : 30;

    if (!actionTaken && Math.abs(deltaY) >= threshold) {
      actionTaken = true;
      if (deltaY > 0 && !isVisible.value) onShow();
      else if (deltaY < 0 && isVisible.value) onHide();
    }
  };

  const onDragEnd = () => {
    dragging = false;
    actionTaken = false;
  };

  const setup = () => {
    document.addEventListener('touchstart', onDragStart, { passive: true });
    // Passive, as in useDockDrag: the band is marked instead, so only a touch
    // starting there waits for the main thread (utils/touchScroll.js).
    document.addEventListener('touchmove', onDragMove, { passive: true });
    if (dragZone.value) unmarkBand = markTouchRegion(dragZone.value);
    if (handle?.value) unmarkHandle = markTouchRegion(handle.value);
    document.addEventListener('touchend', onDragEnd);
    document.addEventListener('mousedown', onDragStart);
    document.addEventListener('mousemove', onDragMove);
    document.addEventListener('mouseup', onDragEnd);
  };

  const teardown = () => {
    document.removeEventListener('touchstart', onDragStart);
    document.removeEventListener('touchmove', onDragMove);
    document.removeEventListener('touchend', onDragEnd);
    document.removeEventListener('mousedown', onDragStart);
    document.removeEventListener('mousemove', onDragMove);
    document.removeEventListener('mouseup', onDragEnd);
    unmarkBand?.();
    unmarkHandle?.();
    unmarkBand = unmarkHandle = null;
  };

  onMounted(setup);
  onUnmounted(teardown);
}
