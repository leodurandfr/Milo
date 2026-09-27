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
 * - loadHardwareInfo()   → GET /hardware-info   (lightweight, loaded by App.vue's resync)
 * - loadHardwareConfig() → GET /hardware-config  (full config + options, used by HardwareSettings)
 */

// Shared global state — lightweight info (screen type/resolution)
const hardwareInfo = ref(null);
const isLoading = ref(false);
let hardwareInfoRequest = null;

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
   * Load lightweight hardware info (screen type/resolution), once: it changes
   * only with a reboot. Resolves true once it is known. A failure leaves it
   * null — screenType already reads that as 'none' — rather than caching a
   * made-up answer that would hide the screen settings until a page reload.
   */
  function loadHardwareInfo() {
    if (hardwareInfo.value) return Promise.resolve(true);
    hardwareInfoRequest ??= (async () => {
      isLoading.value = true;
      try {
        const result = await apiCall.get('/api/settings/hardware-info', {
          category: 'hardware',
          message: 'Error loading hardware info',
          headers: NO_CACHE_HEADERS,
          checkStatus: true
        });
        const hardware = result.ok ? result.data?.hardware : null;
        if (!hardware) return false;
        hardwareInfo.value = hardware;
        logger.debug('hardware', 'Hardware info loaded', hardware);
        return true;
      } finally {
        isLoading.value = false;
        hardwareInfoRequest = null;
      }
    })();
    return hardwareInfoRequest;
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

  const screenType = computed(() => hardwareInfo.value?.screen_type || 'none');
  const rotaryEnabled = computed(() => hardwareConfig.value?.current?.rotary_encoder?.enabled !== false);

  return {
    hardwareInfo,
    isLoading,
    loadHardwareInfo,
    screenType,
    rotaryEnabled,
    // Full config for Hardware settings page
    hardwareConfig,
    isLoadingConfig,
    loadHardwareConfig,
  };
}
