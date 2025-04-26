/**
 * Store principal pour gérer l'état audio global - Version optimisée
 * Ne gère que les aspects globaux (source active, transitions, volume)
 */
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import axios from 'axios';

export * from './snapclient';
export * from './librespot';

export const useAudioStore = defineStore('audio', () => {
  // État global uniquement
  const currentState = ref('none');
  const isTransitioning = ref(false);
  const volume = ref(50);
  const error = ref(null);

  // Getters
  const stateLabel = computed(() => {
    const labels = {
      'none': 'Aucune',
      'transitioning': 'En transition',
      'librespot': 'Spotify Connect',
      'bluetooth': 'Bluetooth',
      'snapclient': 'MacOS',
      'webradio': 'Radio Web'
    };
    return labels[currentState.value] || currentState.value;
  });

  // Actions
  async function fetchState() {
    try {
      const response = await axios.get('/api/audio/state');
      currentState.value = response.data.state;
      isTransitioning.value = response.data.transitioning;
      volume.value = response.data.volume;
      return response.data;
    } catch (err) {
      error.value = 'Erreur de récupération d\'état';
      return null;
    }
  }

  async function changeSource(source) {
    try {
      isTransitioning.value = true;
      const response = await axios.post(`/api/audio/source/${source}`);
      return response.data.status === 'success';
    } catch (err) {
      error.value = 'Erreur de changement de source';
      return false;
    }
  }

  async function setVolume(value) {
    try {
      const response = await axios.post('/api/audio/volume', { volume: value });
      if (response.data.status === 'success') {
        volume.value = value;
        return true;
      }
      return false;
    } catch (err) {
      error.value = 'Erreur de réglage du volume';
      return false;
    }
  }

  // Gestion des événements WebSocket - routage seulement
  function handleWebSocketUpdate(eventType, data) {
    switch (eventType) {
      case 'audio_state_changed':
        currentState.value = data.current_state;
        isTransitioning.value = false;
        break;

      case 'audio_state_changing':
        isTransitioning.value = true;
        break;

      case 'volume_changed':
        volume.value = data.value;
        break;

      case 'audio_error':
        error.value = data.error_message;
        break;
    }
  }

  return {
    // État global
    currentState,
    isTransitioning,
    volume,
    error,
    
    // Getters
    stateLabel,
    
    // Actions
    fetchState,
    changeSource,
    setVolume,
    handleWebSocketUpdate
  };
});