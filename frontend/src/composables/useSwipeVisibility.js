// frontend/src/composables/useSwipeVisibility.js
// Bottom-edge swipe-up-to-show / swipe-down-to-hide gesture for a single panel.
// A smaller sibling of useDockDrag's vertical-swipe logic, without any of the
// Dock-specific concerns (additional apps, volume hold, app hold) — used by the
// Lyrics playback bar, which repurposes the same gesture the Dock normally owns
// (useDockDrag ignores gestures over .lyrics-view for exactly this reason).
import { onMounted, onUnmounted } from 'vue';

export function useSwipeVisibility({ dragZone, panel, isVisible, onShow, onHide }) {
  let dragging = false;
  let startedInBand = false;
  let startY = 0;
  let startTime = 0;
  let actionTaken = false;

  const getEventY = (e) => e.type.includes('touch')
    ? (e.touches?.[0]?.clientY ?? e.changedTouches?.[0]?.clientY ?? e.clientY) : e.clientY;
  const getEventX = (e) => e.type.includes('touch')
    ? (e.touches?.[0]?.clientX ?? e.changedTouches?.[0]?.clientX ?? e.clientX) : e.clientX;

  // Did the gesture start inside the bottom reveal band? Mirrors
  // useDockDrag's pointInBand — the zone stays pointer-events:none, used only
  // as a coordinate marker so it never intercepts taps/scrolls.
  const pointInBand = (e) => {
    const zone = dragZone.value;
    if (!zone) return false;
    const rect = zone.getBoundingClientRect();
    const x = getEventX(e);
    const y = getEventY(e);
    return x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom;
  };

  const onDragStart = (e) => {
    if (isVisible.value) {
      // Bar shown: only a gesture starting on the bar itself can hide it.
      if (!panel.value || !panel.value.contains(e.target)) return;
      startedInBand = false;
    } else {
      // Bar hidden: only a gesture starting in the bottom band can reveal it.
      if (!pointInBand(e)) return;
      startedInBand = true;
    }
    dragging = true;
    startY = getEventY(e);
    startTime = Date.now();
    actionTaken = false;
  };

  const onDragMove = (e) => {
    if (!dragging) return;
    if (startedInBand) e.preventDefault();

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
    document.addEventListener('touchmove', onDragMove, { passive: false });
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
  };

  onMounted(setup);
  onUnmounted(teardown);
}
