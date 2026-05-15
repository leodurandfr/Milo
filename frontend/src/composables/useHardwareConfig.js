// frontend/src/composables/useHardwareConfig.js
import { ref, computed } from 'vue';
import axios from 'axios';
import { logger } from '@/services/logger';

/**
 * Composable to manage system hardware information.
 * State is shared across all composable instances (module-level singleton).
 *
 * Two data paths:
 * - loadHardwareInfo()   → GET /hardware-info   (lightweight, used by InputText, App.vue)
 * - loadHardwareConfig() → GET /hardware-config  (full config + options, used by HardwareSettings)
 */

// Shared global state — lightweight info (screen type/resolution)
const hardwareInfo = ref(null);
const isLoading = ref(false);
const error = ref(null);

// Shared global state — full hardware config + dropdown options
const hardwareConfig = ref(null);
const isLoadingConfig = ref(false);
/**
 * Pre-load hardware config for instant rendering when HardwareSettings opens.
 * Call from SettingsModal.onMounted() (non-blocking, like preloadNetworkStatus).
 */
export async function preloadHardwareConfig() {
  if (hardwareConfig.value || isLoadingConfig.value) return;
  isLoadingConfig.value = true;
  try {
    const response = await axios.get('/api/settings/hardware-config', {
      headers: { 'Cache-Control': 'no-cache', 'Pragma': 'no-cache' }
    });
    if (response.data.status === 'success') {
      hardwareConfig.value = response.data;
    }
  } catch (err) {
    logger.error('hardware', 'Failed to preload hardware config', err);
  } finally {
    isLoadingConfig.value = false;
  }
}

export function useHardwareConfig() {
  /**
   * Load lightweight hardware info (screen type/resolution).
   * Used by InputText.vue, App.vue, etc.
   */
  async function loadHardwareInfo(forceReload = false) {
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
        headers: { 'Cache-Control': 'no-cache', 'Pragma': 'no-cache' }
      });
      if (response.data.status === 'success') {
        hardwareInfo.value = response.data.hardware;
        logger.debug('hardware', 'Hardware info loaded', response.data.hardware);
      } else {
        throw new Error(response.data.message || 'Failed to load hardware info');
      }
      return hardwareInfo.value;
    } catch (err) {
      error.value = err.message;
      logger.error('component', 'Error loading hardware info', err);
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
   * Load full hardware config + dropdown options for the Hardware settings page.
   * Returns { current: {...}, options: { audio_cards: [...], screens: [...] } }
   */
  async function loadHardwareConfig(forceReload = false) {
    if (hardwareConfig.value && !forceReload) {
      return hardwareConfig.value;
    }

    if (isLoadingConfig.value) {
      return new Promise((resolve) => {
        let attempts = 0;
        const checkLoaded = setInterval(() => {
          attempts++;
          if (!isLoadingConfig.value || attempts > 100) {
            clearInterval(checkLoaded);
            resolve(hardwareConfig.value);
          }
        }, 50);
      });
    }

    isLoadingConfig.value = true;

    try {
      const response = await axios.get('/api/settings/hardware-config', {
        headers: { 'Cache-Control': 'no-cache', 'Pragma': 'no-cache' }
      });
      if (response.data.status === 'success') {
        hardwareConfig.value = response.data;
        logger.debug('hardware', 'Hardware config loaded', response.data);
      } else {
        throw new Error('Failed to load hardware config');
      }
      return hardwareConfig.value;
    } catch (err) {
      logger.error('component', 'Error loading hardware config', err);
      return null;
    } finally {
      isLoadingConfig.value = false;
    }
  }

  function reload() {
    return loadHardwareInfo(true);
  }

  const screenType = computed(() => hardwareInfo.value?.screen_type || 'none');
  const screenResolution = computed(() => hardwareInfo.value?.screen_resolution || { width: null, height: null });
  const rotaryEnabled = computed(() => hardwareConfig.value?.current?.rotary_encoder?.enabled !== false);

  return {
    hardwareInfo,
    isLoading,
    error,
    loadHardwareInfo,
    reload,
    screenType,
    screenResolution,
    rotaryEnabled,
    // Full config for Hardware settings page
    hardwareConfig,
    isLoadingConfig,
    loadHardwareConfig,
  };
}
