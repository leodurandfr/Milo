// frontend/src/services/modalDebug.js
/**
 * Verbose debug logging for the Modal scroll/height transition system.
 *
 * Off by default. Flip from the devtools console at any time:
 *   window.__MILO_MODAL_DEBUG = true   // enable
 *   window.__MILO_MODAL_DEBUG = false  // disable
 *
 * Used by Modal.vue, useViewTransition.js, and useAnimatedHeight.js to keep
 * the verbose [Modal/...] / [ViewTransition] / [AnimatedHeight] traces silent
 * in normal use, while still being one keystroke away when investigating
 * scroll/height regressions.
 */

export const isModalDebug = () =>
  typeof window !== 'undefined' && window.__MILO_MODAL_DEBUG === true;

export const modalDebugLog = (...args) => {
  if (isModalDebug()) console.log(...args);
};

export const modalDebugTrace = (label) => {
  if (isModalDebug()) console.trace(label);
};
