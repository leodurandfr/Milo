/**
 * Store principal pour gérer l'état audio de l'application.
 */
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import axios from 'axios';

export * from './snapclient';

export const useAudioStore = defineStore('audio', () => {
  // État
  const currentState = ref('none');
  const isTransitioning = ref(false);
  const metadata = ref({});
  const volume = ref(50);
  const error = ref(null);
  
  // Getters
  const stateLabel = computed(() => {
    const labels = {
      'none': 'Aucune',
      'transitioning': 'En transition',
      'librespot': 'Spotify Connect',
      'bluetooth': 'Bluetooth',
      'macos': 'MacOS',
      'webradio': 'Radio Web'
    };
    
    return labels[currentState.value] || currentState.value;
  });
  
  // Actions
  async function fetchState() {
    try {
      const response = await axios.get('/api/audio/state');
      const data = response.data;
      
      currentState.value = data.state;
      isTransitioning.value = data.transitioning;
      volume.value = data.volume;
      metadata.value = data.metadata || {};
      
      return data;
    } catch (err) {
      console.error('Erreur lors de la récupération de l\'état audio:', err);
      error.value = 'Erreur lors de la récupération de l\'état audio';
      throw err;
    }
  }
  
  async function changeSource(source) {
    try {
      error.value = null;
      isTransitioning.value = true;
      
      const response = await axios.post(`/api/audio/source/${source}`);
      const data = response.data;
      
      if (data.status === 'success') {
        // La mise à jour du currentState sera effectuée par WebSocket
        return true;
      } else {
        throw new Error(data.message || 'Échec du changement de source');
      }
    } catch (err) {
      console.error('Erreur lors du changement de source:', err);
      error.value = err.message || 'Erreur lors du changement de source';
      throw err;
    } finally {
      // Note: isTransitioning devrait être mis à jour par WebSocket
    }
  }
  
  async function setVolume(newVolume) {
    if (newVolume < 0) newVolume = 0;
    if (newVolume > 100) newVolume = 100;
    
    try {
      const response = await axios.post('/api/audio/volume', { volume: newVolume });
      const data = response.data;
      
      if (data.status === 'success') {
        volume.value = newVolume;
        return true;
      } else {
        throw new Error(data.message || 'Échec du changement de volume');
      }
    } catch (err) {
      console.error('Erreur lors du changement de volume:', err);
      error.value = err.message || 'Erreur lors du changement de volume';
      throw err;
    }
  }
  
  async function controlSource(source, command, params = {}) {
    try {
      error.value = null;
      
      const response = await axios.post(`/api/audio/control/${source}`, {
        command,
        data: params
      });
      
      const data = response.data;
      
      if (data.status === 'success') {
        return data.result;
      } else {
        throw new Error(data.message || `Échec de la commande ${command}`);
      }
    } catch (err) {
      console.error(`Erreur lors de l'exécution de la commande ${command} sur ${source}:`, err);
      error.value = err.message || `Erreur lors de l'exécution de la commande ${command}`;
      throw err;
    }
  }
  
  function handleWebSocketUpdate(eventType, data) {
    switch (eventType) {
      case 'audio_state_changed':
        currentState.value = data.current_state;
        isTransitioning.value = data.transitioning === true;
        break;
        
      case 'audio_state_changing':
        isTransitioning.value = true;
        break;
        
      case 'volume_changed':
        volume.value = data.volume;
        break;
        
      case 'audio_error':
        error.value = data.error;
        isTransitioning.value = false;
        break;
        
      case 'audio_metadata_updated':
        if (data.source === currentState.value) {
          metadata.value = data.metadata || {};
        }
        break;
        
      case 'audio_status_updated':
        if (data.source === currentState.value) {
          // Mise à jour de l'état selon le statut
          if (data.plugin_state) {
            // Nouvel état standardisé
            // (Aucune action requise au niveau du store principal)
          }
          
          // Mettre à jour les métadonnées si disponibles
          if (data.metadata) {
            metadata.value = { ...metadata.value, ...data.metadata };
          }
        }
        break;
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
    stateLabel,
    
    // Actions
    fetchState,
    changeSource,
    setVolume,
    controlSource,
    handleWebSocketUpdate
  };
});