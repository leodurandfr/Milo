/**
 * throttleManager - Generic throttle utility for keyed operations
 *
 * Provides throttling for operations identified by a unique key (e.g., filter ID, client ID).
 * Each key gets independent throttle timing.
 *
 * Features:
 * - Per-key throttle state management
 * - Immediate execution if outside throttle window
 * - Delayed execution if within throttle window
 * - Final callback to ensure last value is always sent
 * - Automatic cleanup of completed entries
 */

/**
 * Create a throttle manager for keyed operations
 *
 * @param {Object} options - Configuration options
 * @param {number} options.throttleDelay - Minimum time between immediate executions (ms)
 * @param {number} options.finalDelay - Delay before final execution (ms)
 * @returns {Object} Throttle manager with schedule, clear, and clearAll methods
 *
 * @example
 * const throttle = createThrottleManager({ throttleDelay: 50, finalDelay: 200 });
 *
 * // Schedule a throttled update
 * throttle.schedule('filter_01', () => sendUpdate(filterData));
 *
 * // Clear throttle for specific key (e.g., on slider release)
 * throttle.clear('filter_01');
 *
 * // Clear all throttles (e.g., on component unmount)
 * throttle.clearAll();
 */
export function createThrottleManager(options = {}) {
  const { throttleDelay = 50, finalDelay = 200 } = options;

  // Map of key -> { throttleTimeout, finalTimeout, lastRequestTime }
  const throttleMap = new Map();

  /**
   * Schedule a throttled execution for a key
   *
   * @param {string} key - Unique identifier for this throttled operation
   * @param {Function} callback - Function to execute
   */
  function schedule(key, callback) {
    const now = Date.now();
    let state = throttleMap.get(key) || { lastRequestTime: 0 };

    // Clear existing timeouts
    if (state.throttleTimeout) clearTimeout(state.throttleTimeout);
    if (state.finalTimeout) clearTimeout(state.finalTimeout);

    // Check if we're outside the throttle window
    if (now - state.lastRequestTime >= throttleDelay) {
      // Execute immediately
      callback();
      state.lastRequestTime = now;
    } else {
      // Schedule delayed execution
      const remainingDelay = throttleDelay - (now - state.lastRequestTime);
      state.throttleTimeout = setTimeout(() => {
        callback();
        state.lastRequestTime = Date.now();
      }, remainingDelay);
    }

    // Always schedule final execution to ensure last value is sent
    state.finalTimeout = setTimeout(() => {
      callback();
      // Clean up entry after final execution
      throttleMap.delete(key);
    }, finalDelay);

    throttleMap.set(key, state);
  }

  /**
   * Clear throttle state for a specific key
   *
   * @param {string} key - Key to clear
   */
  function clear(key) {
    const state = throttleMap.get(key);
    if (state) {
      if (state.throttleTimeout) clearTimeout(state.throttleTimeout);
      if (state.finalTimeout) clearTimeout(state.finalTimeout);
      throttleMap.delete(key);
    }
  }

  /**
   * Clear all throttle states
   */
  function clearAll() {
    throttleMap.forEach((state) => {
      if (state.throttleTimeout) clearTimeout(state.throttleTimeout);
      if (state.finalTimeout) clearTimeout(state.finalTimeout);
    });
    throttleMap.clear();
  }

  /**
   * Check if a key has pending throttled operations
   *
   * @param {string} key - Key to check
   * @returns {boolean} True if there are pending operations
   */
  function isPending(key) {
    return throttleMap.has(key);
  }

  /**
   * Get the number of active throttled operations
   *
   * @returns {number} Count of active keys
   */
  function activeCount() {
    return throttleMap.size;
  }

  return {
    schedule,
    clear,
    clearAll,
    isPending,
    activeCount,
  };
}

// Default presets for common use cases
export const THROTTLE_PRESETS = {
  DSP_FILTER: { throttleDelay: 50, finalDelay: 200 },
  VOLUME_FAST: { throttleDelay: 50, finalDelay: 150 },
  VOLUME_MEDIUM: { throttleDelay: 80, finalDelay: 300 },
  VOLUME_SLOW: { throttleDelay: 150, finalDelay: 500 },
};

export default createThrottleManager;
