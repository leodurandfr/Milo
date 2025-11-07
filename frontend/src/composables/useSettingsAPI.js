// frontend/src/composables/useSettingsAPI.js
import { ref } from 'vue';
import axios from 'axios';

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
      await axios.post(`/api/settings/${endpoint}`, payload);
    } catch (error) {
      console.error(`Error updating ${endpoint}:`, error);
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
      updateSetting(endpoint, payload);
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
      console.error(`Error loading config from ${endpoint}:`, error);
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