// frontend/src/composables/useSettingsAPI.js
import { logger } from '@/services/logger';
import { apiCall } from '@/services/apiCall';

/**
 * Composable to manage settings API calls with debouncing
 */
export function useSettingsAPI() {
  const debounceTimers = new Map();

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

  /**
   * Update with debouncing
   * @param {string} key - Unique key to identify the timer
   * @param {string} endpoint - API endpoint
   * @param {object} payload - Data to send
   * @param {number} delay - Delay in ms (default: 800ms)
   */
  function debouncedUpdate(key, endpoint, payload, delay = 800) {
    if (debounceTimers.has(key)) {
      clearTimeout(debounceTimers.get(key));
    }

    const timer = setTimeout(() => {
      // updateSetting rethrows; swallow here so the timer callback does not
      // surface an unhandled rejection (the error was already logged).
      updateSetting(endpoint, payload).catch(() => {});
      debounceTimers.delete(key);
    }, delay);

    debounceTimers.set(key, timer);
  }

  /**
   * Clear all pending timers (for cleanup)
   */
  function clearAllTimers() {
    debounceTimers.forEach(timer => clearTimeout(timer));
    debounceTimers.clear();
  }

  /**
   * Load a configuration from the API
   * @param {string} endpoint - API endpoint
   * @returns {Promise<object>} - API response data
   */
  async function loadConfig(endpoint) {
    const result = await apiCall.get(`/api/settings/${endpoint}`, {
      category: 'api',
      message: `Error loading config from ${endpoint}`,
      rethrow: true
    });
    return result.data;
  }

  return {
    updateSetting,
    debouncedUpdate,
    clearAllTimers,
    loadConfig
  };
}
