// frontend/src/composables/useSettingsAPI.js
import axios from 'axios';
import { logger } from '@/services/logger';

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
    try {
      const response = await axios.put(`/api/settings/${endpoint}`, payload);
      if (response.data?.reload_success === false) {
        logger.warn('api', `Setting ${endpoint} saved but runtime reload failed`);
      }
    } catch (error) {
      logger.error('api', `Error updating ${endpoint}`, error);
      throw error;
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
      updateSetting(endpoint, payload).catch(() => {
        // Error already logged by updateSetting — silenced here to avoid
        // unhandled promise rejection in the timer callback.
      });
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
   * @returns {Promise<object>} - API response
   */
  async function loadConfig(endpoint) {
    try {
      const response = await axios.get(`/api/settings/${endpoint}`);
      return response.data;
    } catch (error) {
      logger.error('api', `Error loading config from ${endpoint}`, error);
      throw error;
    }
  }

  return {
    updateSetting,
    debouncedUpdate,
    clearAllTimers,
    loadConfig
  };
}