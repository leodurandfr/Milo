/**
 * Store spécifique pour Librespot - Version harmonisée
 */
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import axios from 'axios';

export const useLibrespotStore = defineStore('librespot', () => {
  // État standard du plugin
  const pluginState = ref('inactive'); // inactive, ready, connected, error
  
  // États spécifiques à Librespot (lecture)
  const metadata = ref({});
  const isPlaying = ref(false);
  const lastError = ref(null);

  // Getters
  const isConnected = computed(() => pluginState.value === 'connected');
  
  const hasTrackInfo = computed(() => {
    return metadata.value?.title && metadata.value?.artist && isConnected.value;
  });

  // Actions
  async function initialize() {
    try {
      const response = await axios.get('/api/librespot/status');
      
      // Log pour debug
      console.log('Initialisation librespot, données reçues:', response.data);
      
      // Vérifier si connecté
      if (response.data.device_connected) {
        pluginState.value = 'connected';
        
        // Vérifier si des métadonnées sont disponibles
        if (response.data.metadata && Object.keys(response.data.metadata).length > 0) {
          metadata.value = response.data.metadata;
          // Mettre à jour isPlaying basé sur les métadonnées
          isPlaying.value = response.data.metadata.is_playing || false;
        }
        
        // Si pas de métadonnées mais raw_status disponible
        else if (response.data.raw_status) {
          updateFromApiStatus(response.data.raw_status);
        }
      } else {
        pluginState.value = 'inactive';
      }
      
      return true;
    } catch (err) {
      console.error('Erreur d\'initialisation librespot:', err);
      pluginState.value = 'error';
      lastError.value = err.message;
      return false;
    }
  }

  function updateFromApiStatus(status) {
    // Mettre à jour l'état de connexion
    pluginState.value = 'connected';
    
    // Mettre à jour l'état de lecture
    isPlaying.value = !status.paused && !status.stopped;
    
    // Mettre à jour les métadonnées si une piste est présente
    if (status.track) {
      metadata.value = {
        title: status.track.name,
        artist: status.track.artist_names?.join(', ') || 'Artiste inconnu',
        album: status.track.album_name || 'Album inconnu',
        album_art_url: status.track.album_cover_url,
        duration_ms: status.track.duration,
        position_ms: status.track.position || 0,
        uri: status.track.uri,
        is_playing: isPlaying.value
      };
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
    switch (eventType) {
      case 'librespot_metadata_updated':
        if (data.source === 'librespot' && data.metadata) {
          metadata.value = data.metadata;
          // Si on reçoit des métadonnées, on est connecté
          pluginState.value = 'connected';
          // Mettre à jour l'état de lecture
          if (data.metadata.is_playing !== undefined) {
            isPlaying.value = data.metadata.is_playing;
          }
        }
        break;

      case 'librespot_status_updated':
        if (data.source === 'librespot') {
          // Mise à jour de l'état du plugin si fourni
          if (data.plugin_state) {
            pluginState.value = data.plugin_state;
          }
          
          // Gestion des états spécifiques de lecture
          switch (data.status) {
            case 'active':
            case 'connected':
              pluginState.value = 'connected';
              isPlaying.value = data.is_playing || false;
              break;
              
            case 'playing':
              pluginState.value = 'connected';
              isPlaying.value = true;
              break;
              
            case 'paused':
              pluginState.value = 'connected';
              isPlaying.value = false;
              break;
            
            case 'track_ended':
            case 'preparing':
              // États de transition - on ne change pas l'état de lecture
              pluginState.value = 'connected';
              // Ne pas modifier isPlaying pour éviter le clignotement
              break;
              
            case 'inactive':
            case 'stopped':
              // Inactive n'est pas une erreur, juste une déconnexion normale
              pluginState.value = 'inactive';
              isPlaying.value = false;
              metadata.value = {};
              break;
              
            case 'error':
              pluginState.value = 'error';
              isPlaying.value = false;
              lastError.value = data.error_message;
              break;
          }
        }
        break;

      case 'librespot_seek':
        if (data.source === 'librespot' && data.position_ms !== undefined) {
          metadata.value = {
            ...metadata.value,
            position_ms: data.position_ms
          };
        }
        break;
    }
  }

  function clearState() {
    metadata.value = {};
    isPlaying.value = false;
    pluginState.value = 'inactive';
    lastError.value = null;
  }

  return {
    // État standard
    pluginState,
    
    // États spécifiques
    metadata,
    isPlaying,
    lastError,
    
    // Getters
    isConnected,
    hasTrackInfo,
    
    // Actions
    initialize,
    handleCommand,
    handleWebSocketEvent,
    clearState
  };
});