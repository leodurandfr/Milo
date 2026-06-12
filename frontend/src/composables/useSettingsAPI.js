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
  const pending = new Map(); // key → { timerId, endpoint, payload }

  /**
   * Send a settings update to the API
   * @param {string} endpoint - API endpoint (e.g., 'volume-limits')
   * @param {object} payload - Data to send
   */
  async function updateSetting(endpoint, payload) {
    const result = await apiCall.put(`/api/settings/${endpoint}`, payload, {
      category: 'api',
      message: `Error updating ${endpoint}`,
      rethrow: true
    });
    if (result.ok && result.data?.reload_success === false) {
      logger.warn('api', `Setting ${endpoint} saved but runtime reload failed`);
    }
  }

  function flush(key) {
    const entry = pending.get(key);
    if (!entry) return;
    pending.delete(key);
    timer.clear(entry.timerId);
    // updateSetting rethrows; swallow here so the timer callback does not
    // surface an unhandled rejection (the error was already logged).
    updateSetting(entry.endpoint, entry.payload).catch(() => {});
  }

  /**
   * Update with debouncing
   * @param {string} key - Unique key to identify the timer
   * @param {string} endpoint - API endpoint
   * @param {object} payload - Data to send
   * @param {number} delay - Delay in ms (default: 800ms)
   */
  function debouncedUpdate(key, endpoint, payload, delay = 800) {
    const existing = pending.get(key);
    if (existing) {
      timer.clear(existing.timerId);
    }

    const timerId = timer.setTimeout(() => flush(key), delay);
    pending.set(key, { timerId, endpoint, payload });
  }

  onBeforeUnmount(() => {
    for (const key of [...pending.keys()]) flush(key);
  });

  return {
    updateSetting,
    debouncedUpdate
  };
}
