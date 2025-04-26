/**
 * Store principal pour gérer l'état audio global - Version adaptée aux événements unifiés
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
  const pluginState = ref('inactive');
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
      currentState.value = response.data.active_source;
      pluginState.value = response.data.plugin_state;
      isTransitioning.value = response.data.transitioning;
      volume.value = response.data.volume || 50;
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
      isTransitioning.value = false;
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

  // Gestion des événements WebSocket - adaptée aux nouveaux événements
  function handleWebSocketUpdate(eventType, data) {
    switch (eventType) {
      case 'transition_completed':
        currentState.value = data.active_source;
        pluginState.value = data.plugin_state;
        isTransitioning.value = false;
        break;

      case 'transition_started':
        isTransitioning.value = true;
        break;

      case 'plugin_state_changed':
        // Mise à jour uniquement si c'est la source active
        if (data.source === currentState.value) {
          pluginState.value = data.new_state;
        }
        break;

      case 'volume_changed':
        volume.value = data.value;
        break;

      case 'transition_error':
        error.value = data.error;
        isTransitioning.value = false;
        break;
    }
  }

  return {
    // État global
    currentState,
    isTransitioning,
    pluginState,
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