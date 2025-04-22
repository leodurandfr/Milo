/**
 * Store principal pour gérer l'état audio.
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
  const isDisconnected = ref(true);

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

  const isPlaying = computed(() => metadata.value?.is_playing === true);

  // Actions
  async function fetchState() {
    try {
      const response = await axios.get('/api/audio/state');
      currentState.value = response.data.state;
      isTransitioning.value = response.data.transitioning;
      volume.value = response.data.volume;
      metadata.value = response.data.metadata || {};
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

  async function controlSource(source, command, params = {}) {
    try {
      const response = await axios.post(`/api/audio/control/${source}`, {
        command,
        data: params
      });
      return response.data.result || {};
    } catch (err) {
      error.value = `Erreur: ${command}`;
      return {};
    }
  }

  // Gestion des événements WebSocket
  function handleWebSocketUpdate(eventType, data) {
    switch (eventType) {
      case 'audio_state_changed':
        currentState.value = data.current_state;
        isTransitioning.value = false;
        break;

      case 'audio_state_changing':
        isTransitioning.value = true;
        break;

      case 'audio_metadata_updated':
        if (data.source === currentState.value) {
          metadata.value = data.metadata || {};
          isDisconnected.value = false;
        }
        break;

      case 'audio_status_updated':
        if (data.source === currentState.value) {
          // Gestion de la connexion
          if (data.connected === false || data.deviceConnected === false) {
            isDisconnected.value = true;
          } else if (data.connected === true || data.deviceConnected === true) {
            isDisconnected.value = false;
          }

          // Mise à jour des métadonnées
          if (data.metadata) {
            metadata.value = { ...metadata.value, ...data.metadata };
          }

          // Mise à jour de l'état de lecture
          if (data.is_playing !== undefined && metadata.value) {
            metadata.value = { ...metadata.value, is_playing: data.is_playing };
          }
        }
        break;
    }
  }

  return {
    currentState, isTransitioning, metadata, volume, error, isDisconnected,
    stateLabel, isPlaying,
    fetchState, changeSource, controlSource, handleWebSocketUpdate
  };
});