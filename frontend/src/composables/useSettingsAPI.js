// frontend/src/composables/useSettingsAPI.js
import { ref } from 'vue';
import axios from 'axios';

/**
 * Composable pour gérer les appels API des settings avec debouncing
 */
export function useSettingsAPI() {
  const debounceTimers = new Map();

  /**
   * Envoie une mise à jour de setting à l'API
   * @param {string} endpoint - L'endpoint API (ex: 'volume-limits')
   * @param {object} payload - Les données à envoyer
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
   * Mise à jour avec debouncing
   * @param {string} key - Clé unique pour identifier le timer
   * @param {string} endpoint - L'endpoint API
   * @param {object} payload - Les données à envoyer
   * @param {number} delay - Délai en ms (défaut: 800ms)
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
   * Nettoie tous les timers en attente (pour cleanup)
   */
  function clearAllTimers() {
    debounceTimers.forEach(timer => clearTimeout(timer));
    debounceTimers.clear();
  }

  /**
   * Charge une configuration depuis l'API
   * @param {string} endpoint - L'endpoint API
   * @returns {Promise<object>} - La réponse de l'API
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
