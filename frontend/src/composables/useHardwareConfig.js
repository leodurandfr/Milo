// frontend/src/composables/useHardwareConfig.js
import { ref, computed } from 'vue';
import axios from 'axios';

/**
 * Composable pour gérer les informations hardware du système
 * État partagé entre toutes les instances du composable
 */

// État global partagé
const hardwareInfo = ref(null);
const isLoading = ref(false);
const error = ref(null);

export function useHardwareConfig() {
  /**
   * Charge les informations hardware depuis l'API
   * Les données sont mises en cache après le premier chargement
   * @param {boolean} forceReload - Force le rechargement même si les données sont en cache
   */
  async function loadHardwareInfo(forceReload = false) {
    // Si déjà chargé et pas de forceReload, retourner immédiatement
    if (hardwareInfo.value && !forceReload) {
      return hardwareInfo.value;
    }

    // Si déjà en cours de chargement, attendre
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
        // Désactiver le cache du navigateur pour toujours obtenir les données fraîches
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
      // Valeur par défaut en cas d'erreur
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
   * Force le rechargement des données hardware
   */
  function reload() {
    return loadHardwareInfo(true);
  }

  /**
   * Computed properties pour un accès facile
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
