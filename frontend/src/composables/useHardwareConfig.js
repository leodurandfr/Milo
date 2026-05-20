// frontend/src/composables/useHardwareConfig.js
import { ref, computed } from 'vue';
import { logger } from '@/services/logger';
import { apiCall } from '@/services/apiCall';
import { useTimer } from '@/composables/useTimer';

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

const NO_CACHE_HEADERS = { 'Cache-Control': 'no-cache', 'Pragma': 'no-cache' };

/**
 * Pre-load hardware config for instant rendering when HardwareSettings opens.
 * Call from SettingsModal.onMounted() (non-blocking, like preloadNetworkStatus).
 */
export async function preloadHardwareConfig() {
  if (hardwareConfig.value || isLoadingConfig.value) return;
  isLoadingConfig.value = true;
  const result = await apiCall.get('/api/settings/hardware-config', {
    category: 'hardware',
    message: 'Failed to preload hardware config',
    headers: NO_CACHE_HEADERS,
    checkStatus: true
  });
  if (result.ok) {
    hardwareConfig.value = result.data;
  }
  isLoadingConfig.value = false;
}

export function useHardwareConfig() {
  const timer = useTimer();

  /**
   * Load lightweight hardware info (screen type/resolution).
   * Used by InputText.vue, App.vue, etc.
   */
  async function loadHardwareInfo(forceReload = false) {
    if (hardwareInfo.value && !forceReload) {
      return hardwareInfo.value;
    }

    if (isLoading.value) {
      return new Promise((resolve) => {
        const checkLoaded = timer.setInterval(() => {
          if (!isLoading.value) {
            timer.clear(checkLoaded);
            resolve(hardwareInfo.value);
          }
        }, 50);
      });
    }

    isLoading.value = true;
    error.value = null;

    const result = await apiCall.get('/api/settings/hardware-info', {
      category: 'hardware',
      message: 'Error loading hardware info',
      headers: NO_CACHE_HEADERS,
      checkStatus: true
    });

    if (result.ok) {
      hardwareInfo.value = result.data.hardware;
      logger.debug('hardware', 'Hardware info loaded', result.data.hardware);
    } else {
      error.value = result.error?.detail || 'Failed to load hardware info';
      hardwareInfo.value = {
        screen_type: 'none',
        screen_resolution: { width: null, height: null }
      };
    }
    isLoading.value = false;
    return hardwareInfo.value;
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
        const checkLoaded = timer.setInterval(() => {
          attempts++;
          if (!isLoadingConfig.value || attempts > 100) {
            timer.clear(checkLoaded);
            resolve(hardwareConfig.value);
          }
        }, 50);
      });
    }

    isLoadingConfig.value = true;

    const result = await apiCall.get('/api/settings/hardware-config', {
      category: 'hardware',
      message: 'Error loading hardware config',
      headers: NO_CACHE_HEADERS,
      checkStatus: true
    });
    if (result.ok) {
      hardwareConfig.value = result.data;
      logger.debug('hardware', 'Hardware config loaded', result.data);
    } else {
      hardwareConfig.value = null;
    }
    isLoadingConfig.value = false;
    return hardwareConfig.value;
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
