// frontend/src/composables/useSettingsAPI.js
import { onBeforeUnmount } from 'vue';
import { logger } from '@/services/logger';
import { apiCall } from '@/services/apiCall';
import { useTimer } from '@/composables/useTimer';

/**
 * Composable to manage settings API calls with debouncing.
 * Debounced updates still pending on unmount are flushed (sent immediately),
 * never dropped — navigating away must not lose a write.
 */
export function useSettingsAPI() {
  const timer = useTimer();
  const pending = new Map(); // key → { timerId, endpoint, payload, onError }

  /**
   * Send a settings update to the API
   * @param {string} endpoint - API endpoint (e.g., 'volume-limits')
   * @param {object} payload - Data to send
   * @returns {Promise<{applied: boolean}>} `applied` is false when the server
   *   saved the setting but could not apply it at runtime (`reload_success`),
   *   or the request was cancelled; a request that failed outright rejects.
   */
  async function updateSetting(endpoint, payload) {
    const result = await apiCall.put(`/api/settings/${endpoint}`, payload, {
      category: 'api',
      message: `Error updating ${endpoint}`,
      rethrow: true
    });
    const reloadFailed = result.data?.reload_success === false;
    if (result.ok && reloadFailed) {
      logger.warn('api', `Setting ${endpoint} saved but runtime reload failed`);
    }
    return { applied: result.ok && !reloadFailed };
  }

  function flush(key) {
    const entry = pending.get(key);
    if (!entry) return;
    pending.delete(key);
    timer.clear(entry.timerId);
    // updateSetting rethrows (the error is already logged); the caller's
    // onError is how a screen that moved optimistically learns of it — unless
    // a newer write for the same key is queued, which speaks for the screen now.
    updateSetting(entry.endpoint, entry.payload).catch((error) => {
      if (!pending.has(key)) entry.onError?.(error);
    });
  }

  /**
   * Update with debouncing
   * @param {string} key - Unique key to identify the timer
   * @param {string} endpoint - API endpoint
   * @param {object} payload - Data to send
   * @param {number} delay - Delay in ms (default: 800ms)
   * @param {object} [options]
   * @param {(error: Error) => void} [options.onError] - Called when the write
   *   is rejected, including one flushed on unmount, unless a newer write for
   *   the same key is pending by then
   */
  function debouncedUpdate(key, endpoint, payload, delay = 800, { onError } = {}) {
    const existing = pending.get(key);
    if (existing) {
      timer.clear(existing.timerId);
    }

    const timerId = timer.setTimeout(() => flush(key), delay);
    pending.set(key, { timerId, endpoint, payload, onError });
  }

  onBeforeUnmount(() => {
    for (const key of [...pending.keys()]) flush(key);
  });

  return {
    updateSetting,
    debouncedUpdate
  };
}
