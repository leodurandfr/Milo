/**
 * useVolumeThrottle - Throttle for volume sliders
 *
 * - MEDIUM: the zone header slider
 * - FAST: a client's own slider
 *
 * One core for both shapes: at most one call per throttle window, plus one
 * trailing call carrying the value the gesture ended on, and `flush()` to send
 * that value at once on release. The value a gesture ends on is emitted
 * exactly once.
 *
 * Timer-primitive layer: like useTimer, this composable manages its own cleanup,
 * so it uses raw window.* timers directly (the window.* prefix marks the
 * deliberate raw usage — see the no-restricted-globals rule in eslint.config.mjs).
 */

import { onUnmounted } from 'vue';

// Throttle presets (in milliseconds)
const THROTTLE_PRESETS = {
  FAST: { throttle: 50, final: 150 },
  MEDIUM: { throttle: 80, final: 300 },
};

function createThrottle(callback, { throttle, final }) {
  let finalTimer = null;
  let lastArgs = null;
  let lastCallTime = 0;

  const clearFinal = () => {
    if (finalTimer) {
      window.clearTimeout(finalTimer);
      finalTimer = null;
    }
  };

  const call = (...args) => {
    const now = Date.now();
    lastArgs = args;
    clearFinal();

    if (now - lastCallTime >= throttle) {
      // Sent now, so nothing is left for the trailing timer or a release to
      // send again: a second emit of one value is one more fan-out to every
      // speaker for nothing.
      lastArgs = null;
      lastCallTime = now;
      callback(...args);
    }

    finalTimer = window.setTimeout(flush, final);
  };

  const flush = () => {
    clearFinal();
    if (lastArgs) {
      const args = lastArgs;
      lastArgs = null;
      callback(...args);
    }
  };

  const cancel = () => {
    clearFinal();
    lastArgs = null;
  };

  return { call, flush, cancel };
}

/**
 * @param {Function} callback - The function to throttle
 * @param {'FAST'|'MEDIUM'} preset
 * @returns {{throttledFn: Function, flush: Function}}
 */
export function useVolumeThrottle(callback, preset = 'MEDIUM') {
  const throttle = createThrottle(callback, THROTTLE_PRESETS[preset]);
  onUnmounted(throttle.cancel);
  return { throttledFn: throttle.call, flush: throttle.flush };
}

/**
 * One throttle per key (a client's mac), each with its own window.
 *
 * @param {Function} callbackFactory - (key) => callback
 * @param {'FAST'|'MEDIUM'} preset
 * @returns {{getThrottledFn: Function, flush: Function}}
 */
export function useVolumeThrottleMap(callbackFactory, preset = 'MEDIUM') {
  const throttles = new Map();

  const throttleFor = (key) => {
    if (!throttles.has(key)) {
      throttles.set(key, createThrottle(callbackFactory(key), THROTTLE_PRESETS[preset]));
    }
    return throttles.get(key);
  };

  onUnmounted(() => {
    throttles.forEach((throttle) => throttle.cancel());
    throttles.clear();
  });

  return {
    getThrottledFn: (key) => throttleFor(key).call,
    flush: (key) => throttles.get(key)?.flush(),
  };
}
