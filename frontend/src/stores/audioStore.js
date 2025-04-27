// frontend/src/stores/audioStore.js
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import axios from 'axios';

export const useAudioStore = defineStore('audio', () => {
  // État centralisé
  const systemState = ref({
    active_source: 'none',
    plugin_state: 'inactive',
    transitioning: false,
    metadata: {},
    error: null
  });
  
  const isLoading = ref(false);
  const error = ref(null);
  
  // Getters
  const currentSource = computed(() => systemState.value.active_source);
  const pluginState = computed(() => systemState.value.plugin_state);
  const isTransitioning = computed(() => systemState.value.transitioning);
  const metadata = computed(() => systemState.value.metadata || {});
  
  const stateLabel = computed(() => {
    const labels = {
      'none': 'Aucune',
      'librespot': 'Spotify Connect',
      'bluetooth': 'Bluetooth',
      'snapclient': 'MacOS',
      'webradio': 'Radio Web'
    };
    return labels[currentSource.value] || currentSource.value;
  });
  
  // Actions
  async function fetchSystemState() {
    try {
      isLoading.value = true;
      const response = await axios.get('/api/audio/state');
      systemState.value = response.data;
      error.value = null;
      return systemState.value;
    } catch (err) {
      error.value = err.message;
      throw err;
    } finally {
      isLoading.value = false;
    }
  }
  
  async function changeSource(source) {
    try {
      isLoading.value = true;
      const response = await axios.post(`/api/audio/source/${source}`);
      if (response.data.status === 'success') {
        // L'état sera mis à jour via WebSocket
        return true;
      }
      return false;
    } catch (err) {
      error.value = err.message;
      return false;
    } finally {
      isLoading.value = false;
    }
  }
  
  function handleWebSocketUpdate(data) {
    // Mise à jour de l'état avec les données reçues
    if (data.full_state) {
      systemState.value = data.full_state;
    }
  }
  
  return {
    // État
    systemState,
    isLoading,
    error,
    
    // Getters
    currentSource,
    pluginState,
    isTransitioning,
    metadata,
    stateLabel,
    
    // Actions
    fetchSystemState,
    changeSource,
    handleWebSocketUpdate
  };
});