// frontend/src/composables/useHardwareConfig.js
import { ref, computed } from 'vue';
import axios from 'axios';

/**
 * Composable to manage system hardware information
 * State is shared across all composable instances
 */

// Shared global state
const hardwareInfo = ref(null);
const isLoading = ref(false);
const error = ref(null);

export function useHardwareConfig() {
  /**
   * Load hardware information from the API
   * Data is cached after the first load
   * @param {boolean} forceReload - Force reload even if data is cached
   */
  async function loadHardwareInfo(forceReload = false) {
    // If already loaded and no forceReload, return immediately
    if (hardwareInfo.value && !forceReload) {
      return hardwareInfo.value;
    }

    // If already loading, wait
    if (isLoading.value) {
      return new Promise((resolve) => {
        const checkLoaded = setInterval(() => {
          if (!isLoading.value) {
            clearInterval(checkLoaded);
            resolve(hardwareInfo.value);
          }
        }, 50);
      });
    }

    isLoading.value = true;
    error.value = null;

    try {
      const response = await axios.get('/api/settings/hardware-info', {
        // Disable browser cache to always get fresh data
        headers: {
          'Cache-Control': 'no-cache',
          'Pragma': 'no-cache'
        }
      });
      if (response.data.status === 'success') {
        hardwareInfo.value = response.data.hardware;
        console.log('[HardwareConfig] Loaded:', response.data.hardware);
      } else {
        throw new Error(response.data.message || 'Failed to load hardware info');
      }
      return hardwareInfo.value;
    } catch (err) {
      error.value = err.message;
      console.error('Error loading hardware info:', err);
      // Default value in case of error
      hardwareInfo.value = {
        screen_type: 'none',
        screen_resolution: { width: null, height: null }
      };
      return hardwareInfo.value;
    } finally {
      isLoading.value = false;
    }
  }

  /**
   * Force reload of hardware data
   */
  function reload() {
    return loadHardwareInfo(true);
  }

  /**
   * Computed properties for easy access
   */
  const screenType = computed(() => hardwareInfo.value?.screen_type || 'none');
  const screenResolution = computed(() => hardwareInfo.value?.screen_resolution || { width: null, height: null });

  return {
    hardwareInfo,
    isLoading,
    error,
    loadHardwareInfo,
    reload,
    screenType,
    screenResolution
  };
}