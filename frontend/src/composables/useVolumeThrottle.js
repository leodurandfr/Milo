/**
 * useVolumeThrottle - Unified throttle manager for volume interactions
 *
 * Provides consistent throttling across all volume controls:
 * - FAST: Mobile dock buttons, quick adjustments
 * - MEDIUM: Zone sliders, multiroom controls
 * - SLOW: Settings changes
 *
 * Features:
 * - Throttled execution (skip calls within throttle window)
 * - Final callback (ensures last value is sent after drag ends)
 * - Proper cleanup on unmount
 *
 * Timer-primitive layer: like useTimer, this composable manages its own cleanup,
 * so it uses raw window.* timers directly (the window.* prefix marks the
 * deliberate raw usage — see the no-restricted-globals rule in eslint.config.mjs).
 */

import { ref, onUnmounted } from 'vue';

// Throttle presets (in milliseconds)
const THROTTLE_PRESETS = {
  FAST: { throttle: 50, final: 150 },
  MEDIUM: { throttle: 80, final: 300 },
  SLOW: { throttle: 150, final: 500 },
};

/**
 * Create a throttled volume control function
 *
 * @param {Function} callback - The function to throttle
 * @param {string} preset - Preset name: 'FAST', 'MEDIUM', or 'SLOW'
 * @returns {Object} { throttledFn, cleanup, isThrottling }
 */
export function useVolumeThrottle(callback, preset = 'MEDIUM') {
  const config = THROTTLE_PRESETS[preset] || THROTTLE_PRESETS.MEDIUM;

  // State
  const isThrottling = ref(false);
  let finalTimer = null;
  let lastArgs = null;
  let lastCallTime = 0;

  /**
   * Throttled function - call this instead of the original callback
   * @param {...any} args - Arguments to pass to the callback
   */
  const throttledFn = (...args) => {
    const now = Date.now();
    lastArgs = args;

    // Clear any pending final timer
    if (finalTimer) {
      window.clearTimeout(finalTimer);
      finalTimer = null;
    }

    // Check if we're within the throttle window
    if (now - lastCallTime >= config.throttle) {
      // Execute immediately
      lastCallTime = now;
      isThrottling.value = true;
      callback(...args);
    }

    // Schedule final callback to ensure last value is sent
    finalTimer = window.setTimeout(() => {
      if (lastArgs) {
        callback(...lastArgs);
        lastArgs = null;
      }
      isThrottling.value = false;
    }, config.final);
  };

  /**
   * Force execute with current args (useful for slider release)
   */
  const flush = () => {
    if (finalTimer) {
      window.clearTimeout(finalTimer);
      finalTimer = null;
    }
    if (lastArgs) {
      callback(...lastArgs);
      lastArgs = null;
    }
    isThrottling.value = false;
  };

  /**
   * Cleanup all timers
   */
  const cleanup = () => {
    if (finalTimer) {
      window.clearTimeout(finalTimer);
      finalTimer = null;
    }
    lastArgs = null;
    isThrottling.value = false;
  };

  // Auto-cleanup on component unmount
  onUnmounted(cleanup);

  return {
    throttledFn,
    flush,
    cleanup,
    isThrottling,
  };
}

/**
 * Create a map of throttled functions (for per-client throttling)
 *
 * @param {Function} callbackFactory - Factory function (key) => callback
 * @param {string} preset - Preset name
 * @returns {Object} { getThrottledFn }
 */
export function useVolumeThrottleMap(callbackFactory, preset = 'MEDIUM') {
  const config = THROTTLE_PRESETS[preset] || THROTTLE_PRESETS.MEDIUM;
  const throttleMap = new Map();

  /**
   * Get or create a throttled function for a specific key
   * @param {string} key - Unique identifier (e.g., client ID)
   * @returns {Function} Throttled function
   */
  const getThrottledFn = (key) => {
    if (!throttleMap.has(key)) {
      let lastCallTime = 0;
      let finalTimer = null;
      let lastArgs = null;

      const throttledFn = (...args) => {
        const now = Date.now();
        lastArgs = args;

        if (finalTimer) {
          window.clearTimeout(finalTimer);
        }

        if (now - lastCallTime >= config.throttle) {
          lastCallTime = now;
          callbackFactory(key)(...args);
        }

        finalTimer = window.setTimeout(() => {
          if (lastArgs) {
            callbackFactory(key)(...lastArgs);
            lastArgs = null;
          }
        }, config.final);
      };

      throttleMap.set(key, {
        fn: throttledFn,
        cleanup: () => {
          if (finalTimer) {
            window.clearTimeout(finalTimer);
          }
        },
      });
    }

    return throttleMap.get(key).fn;
  };

  /**
   * Cleanup all throttled functions
   */
  const cleanupAll = () => {
    throttleMap.forEach((entry) => entry.cleanup());
    throttleMap.clear();
  };

  // Auto-cleanup on component unmount
  onUnmounted(cleanupAll);

  return {
    getThrottledFn,
  };
}

