import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import axios from 'axios';

export const useAudioStore = defineStore('audio', () => {
  // État
  const currentState = ref('none');
  const isTransitioning = ref(false);
  const metadata = ref({});
  const volume = ref(50);
  const error = ref(null);
  
  // Getters
  const isPlaying = computed(() => {
    return currentState.value === 'librespot' && metadata.value?.is_playing;
  });
  
  const stateLabel = computed(() => {
    switch (currentState.value) {
      case 'librespot': return 'Spotify';
      case 'bluetooth': return 'Bluetooth';
      case 'macos': return 'MacOS';
      case 'webradio': return 'Web Radio';
      default: return 'Aucune source';
    }
  });
  
  // Actions
  async function fetchState() {
    try {
      const response = await axios.get('/api/audio/state');
      updateState(response.data);
      return response.data;
    } catch (err) {
      error.value = 'Erreur lors de la récupération de l\'état audio';
      console.error(error.value, err);
    }
  }
  
  async function changeSource(source) {
    try {
      error.value = null;
      isTransitioning.value = true;
      const response = await axios.post(`/api/audio/source/${source}`);
      if (response.data.status === 'error') {
        throw new Error(response.data.message);
      }
      return true;
    } catch (err) {
      error.value = `Erreur lors du changement de source: ${err.message}`;
      console.error(error.value);
      return false;
    } finally {
      // L'état sera mis à jour via WebSocket
    }
  }
  
  // Méthode générique pour contrôler une source audio
  async function controlSource(source, command, data = {}) {
    try {
      error.value = null;
      const response = await axios.post(`/api/audio/control/${source}`, {
        command,
        data
      });
      
      if (response.data.status === 'error') {
        throw new Error(response.data.message);
      }
      
      return true;
    } catch (err) {
      error.value = `Erreur lors du contrôle de la source ${source}: ${err.message}`;
      console.error(error.value);
      return false;
    }
  }
  
  function updateState(stateData) {
    currentState.value = stateData.state;
    isTransitioning.value = stateData.transitioning;
    if (stateData.metadata) {
      metadata.value = stateData.metadata;
    }
    volume.value = stateData.volume;
  }
  
  function handleWebSocketUpdate(eventType, data) {
    if (eventType === 'audio_state_changed') {
      currentState.value = data.current_state;
      isTransitioning.value = data.transitioning;
    } else if (eventType === 'volume_changed') {
      volume.value = data.volume;
    } else if (eventType === 'audio_error') {
      error.value = data.error;
    } else if (eventType === 'audio_metadata_updated') {
      metadata.value = data.metadata;
    } else if (eventType === 'audio_status_updated') {
      // Mise à jour du statut (connecté, déconnecté, etc.)
      if (data.source === currentState.value) {
        // Mettre à jour les métadonnées spécifiques à la source
        if (data.source === 'bluetooth' || data.source === 'macos') {
          metadata.value = {
            ...metadata.value,
            deviceConnected: data.status === 'connected',
            deviceName: data.deviceName || 'Périphérique inconnu'
          };
        }
      }
    }
  }
  
  return {
    // État
    currentState,
    isTransitioning,
    metadata,
    volume,
    error,
    
    // Getters
    isPlaying,
    stateLabel,
    
    // Actions
    fetchState,
    changeSource,
    updateState,
    handleWebSocketUpdate,
    controlSource
  };
});