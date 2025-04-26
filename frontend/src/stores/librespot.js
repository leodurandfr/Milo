// frontend/src/stores/librespot.js
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import axios from 'axios';

export const useLibrespotStore = defineStore('librespot', () => {
  // État spécifique à Librespot
  const metadata = ref({});
  const isPlaying = ref(false);
  const deviceConnected = ref(false);
  const lastError = ref(null);

  // Getters
  const hasTrackInfo = computed(() => {
    return metadata.value?.title && metadata.value?.artist && deviceConnected.value;
  });

  // Actions
  async function initialize() {
    try {
      const response = await axios.get('/librespot/status');
      
      console.log('Librespot status response:', response.data);
      
      // Forcer la mise à jour de tous les états
      deviceConnected.value = response.data.device_connected === true;
      isPlaying.value = response.data.is_playing === true;
      
      // Mettre à jour les métadonnées de manière plus robuste
      if (response.data.metadata && typeof response.data.metadata === 'object') {
        // S'assurer que les métadonnées ont au moins le titre et l'artiste
        const { title, artist, album, album_art_url, duration, position } = response.data.metadata;
        
        if (title && artist) {
          metadata.value = {
            title,
            artist,
            album: album || '',
            album_art_url: album_art_url || '',
            duration: duration || 0,
            position: position || 0
          };
        } else {
          metadata.value = response.data.metadata;
        }
      }
      
      // Logger pour debug
      console.log('État après initialisation:', {
        deviceConnected: deviceConnected.value,
        isPlaying: isPlaying.value,
        hasTrackInfo: hasTrackInfo.value,
        metadata: metadata.value
      });
      
      return true;
    } catch (err) {
      console.error('Erreur d\'initialisation librespot:', err);
      lastError.value = err.message;
      return false;
    }
  }

  async function handleCommand(command, data = {}) {
    try {
      const response = await axios.post('/api/audio/control/librespot', {
        command,
        data
      });
      
      if (response.data.status === 'success') {
        // Pour play/pause, mettre à jour l'état localement
        if (command === 'play' || command === 'resume') {
          isPlaying.value = true;
        } else if (command === 'pause') {
          isPlaying.value = false;
        }
        return true;
      }
      return false;
    } catch (err) {
      console.error(`Erreur commande ${command}:`, err);
      lastError.value = err.message;
      return false;
    }
  }

  function handleWebSocketEvent(eventType, data) {
    if (eventType === 'plugin_state_changed' && data.source === 'librespot') {
      // Mettre à jour l'état de connexion
      if (data.new_state === 'connected') {
        deviceConnected.value = true;
      } else if (data.new_state === 'ready' || data.new_state === 'inactive') {
        deviceConnected.value = false;
        // Si on n'est plus connecté, on réinitialise tout
        if (data.new_state === 'inactive') {
          metadata.value = {};
          isPlaying.value = false;
        }
      }
      
      // Mettre à jour en fonction des métadonnées
      if (data.metadata) {
        metadata.value = data.metadata;
        
        // Mettre à jour l'état de lecture si fourni
        if (data.metadata.is_playing !== undefined) {
          isPlaying.value = data.metadata.is_playing;
        }
        
        // Gérer les statuts spécifiques
        if (data.metadata.status === 'playing') {
          isPlaying.value = true;
        } else if (data.metadata.status === 'paused') {
          isPlaying.value = false;
        }
      }
    }
  }

  function clearState() {
    metadata.value = {};
    isPlaying.value = false;
    deviceConnected.value = false;
    lastError.value = null;
  }

  return {
    // État
    metadata,
    isPlaying,
    deviceConnected,
    lastError,
    
    // Getters
    hasTrackInfo,
    
    // Actions
    initialize,
    handleCommand,
    handleWebSocketEvent,
    clearState
  };
});