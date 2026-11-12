// frontend/src/utils/touchScroll.js
// Holding the page still under one touch gesture, without making every other
// touch wait for this thread.
//
// A non-passive touchmove listener on document lets a gesture cancel the
// browser's scroll, but it makes the whole page a blocking region: every touch,
// anywhere, then waits for the main thread before it may start scrolling — one
// frame at rest, up to ~180 ms while the thread is busy (measured 2026-11-12,
// Chromium 149). The page only needs it under one small band.
//
// Chromium decides whether a touch sequence blocks from the region its
// touchstart lands in, and re-checks only the first move, against the listener
// regions the compositor received at its last commit — so a listener added from
// the touchstart arrives after the moves it was meant for. Once the touchstart
// blocks, though, every move of that sequence goes to the main thread, in order,
// and meets whatever listener the touchstart added. Hence two halves:
//
//   markTouchRegion(el)  a no-op non-passive touchstart on `el`: only touches
//                        starting over it wait for the main thread. It works
//                        under pointer-events: none — Chromium records the region
//                        all the same — and the touch still lands on what lies
//                        beneath, so a tap or a press there is untouched.
//   holdScroll()         called from that touchstart: cancels the browser's
//                        scroll until every finger is up.
//
// WebKit leaves a pointer-events: none element out of its touch regions, so on
// an iPhone the hold may come too late and the page scroll along with the swipe.

const noop = () => {};

/** Makes touches starting over `el` blocking. Returns the function that undoes it. */
export function markTouchRegion(el) {
  el.addEventListener('touchstart', noop, { passive: false });
  return () => el.removeEventListener('touchstart', noop);
}

const preventScroll = (e) => {
  if (e.cancelable) e.preventDefault();
};

function unhold() {
  document.removeEventListener('touchmove', preventScroll);
  document.removeEventListener('touchend', release);
  document.removeEventListener('touchcancel', release);
  document.removeEventListener('touchstart', releaseStale, true);
}

const release = (e) => {
  if (e.touches.length === 0) unhold();
};

// The end of a sequence can miss document: a touch keeps its first target, and
// one removed mid-gesture (a row re-rendered) dispatches to a detached node. A
// hold left on would cancel every later scroll, page-wide — so the next first
// finger down ends it, before its own touchstart may arm a new one.
const releaseStale = (e) => {
  if (e.touches.length === 1) unhold();
};

/** From a touchstart over a marked region: no scroll until the last finger lifts. */
export function holdScroll() {
  document.addEventListener('touchmove', preventScroll, { passive: false });
  document.addEventListener('touchend', release, { passive: true });
  document.addEventListener('touchcancel', release, { passive: true });
  document.addEventListener('touchstart', releaseStale, { capture: true, passive: true });
}
