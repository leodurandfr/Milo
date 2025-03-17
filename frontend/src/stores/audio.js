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
  const lastKnownConnectedState = ref(false);
  const lastKnownGoodMetadata = ref({});
  const isDisconnected = ref(true);

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
      console.log(`Envoi de la commande "${command}" à la source "${source}"`, data);

      // Utiliser le bon endpoint
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

  // Fonction pour vérifier si les métadonnées sont valides et contiennent des informations utiles
  function hasValidMetadata(meta) {
    return meta && (
      meta.title ||
      meta.artist ||
      meta.album_art_url ||
      meta.duration_ms
    );
  }

  function updateState(stateData) {
    currentState.value = stateData.state;
    isTransitioning.value = stateData.transitioning;

    if (stateData.metadata) {
      // Préserver les métadonnées valides
      if (hasValidMetadata(stateData.metadata)) {
        lastKnownGoodMetadata.value = { ...stateData.metadata };
      }

      metadata.value = stateData.metadata;
    }

    volume.value = stateData.volume;
  }

  function updateMetadataFromStatus(metadataFromStatus) {
    // Vérifier que les données sont valides
    if (!metadataFromStatus || !metadataFromStatus.title) {
      console.warn("Tentative de mise à jour des métadonnées avec des données invalides");
      return;
    }

    console.log("Mise à jour des métadonnées depuis le statut:", metadataFromStatus);

    // Mettre à jour les métadonnées
    metadata.value = metadataFromStatus;

    // Mise à jour de l'état de connexion
    if (metadataFromStatus.connected !== undefined) {
      isDisconnected.value = !metadataFromStatus.connected;
    }

    // Sauvegarder comme dernières métadonnées valides
    if (hasValidMetadata(metadataFromStatus)) {
      lastKnownGoodMetadata.value = { ...metadataFromStatus };
    }
  }

  // Fonction pour effacer les métadonnées lors d'une déconnexion
  function clearMetadata() {
    console.log("Effacement des métadonnées suite à déconnexion");
    metadata.value = {};
    isDisconnected.value = true;
  }

  function handleWebSocketUpdate(eventType, data) {
    console.log(`WebSocket event received: ${eventType}`, data);

    if (eventType === 'audio_state_changed') {
      currentState.value = data.current_state;
      isTransitioning.value = data.transitioning;

      // Si l'état change à 'none', effacer les métadonnées
      if (data.current_state === 'none') {
        clearMetadata();
      }

    } else if (eventType === 'volume_changed') {
      volume.value = data.volume;

    } else if (eventType === 'audio_error') {
      error.value = data.error;

    } else if (eventType === 'audio_metadata_updated') {
      console.log('Metadata update received:', data.metadata);

      // S'assurer que les métadonnées sont bien attachées à la source actuelle
      if (data.source === currentState.value) {
        // Si nous recevons des métadonnées, nous ne sommes probablement pas déconnectés
        isDisconnected.value = false;

        // MODIFICATION: Fusionner avec les métadonnées existantes au lieu de remplacer
        if (Object.keys(data.metadata).length > 0) {
          // Préserver les métadonnées valides existantes
          const mergedMetadata = {
            ...metadata.value,  // Garder les anciennes métadonnées
            ...data.metadata     // Ajouter les nouvelles
          };

          // Si nous avons des informations valides (titre, artiste), les stocker comme dernières bonnes métadonnées
          if (hasValidMetadata(mergedMetadata)) {
            lastKnownGoodMetadata.value = { ...mergedMetadata };
          }

          // Toujours mettre à jour les métadonnées même si incomplètes
          metadata.value = mergedMetadata;

          // Mettre à jour l'état de connexion
          if (data.metadata.connected !== false) {
            lastKnownConnectedState.value = true;
          }
        } else {
          console.log('Empty metadata update ignored');
        }
      }

    } else if (eventType === 'audio_status_updated') {
      console.log('Status update received:', data);

      // Mise à jour du statut (connecté, déconnecté, etc.)
      if (data.source === currentState.value) {
        // *** DÉCONNEXION EXPLICITE ***
        // Si nous recevons un statut disconnected, effacer les métadonnées
        if (data.status === 'disconnected' || data.connected === false) {
          console.log("Déconnexion explicite détectée, effacement des métadonnées");
          clearMetadata();
          lastKnownConnectedState.value = false;
          return;
        }

        // Vérifier si le statut indique une connexion
        const isConnectedStatus =
          data.status === 'connected' ||
          data.status === 'playing' ||
          data.status === 'paused' ||
          data.status === 'active' ||
          data.connected === true ||
          data.deviceConnected === true ||
          data.is_playing === true;

        // Si le statut indique une connexion, mettre à jour l'état
        if (isConnectedStatus) {
          lastKnownConnectedState.value = true;
          isDisconnected.value = false;
        }

        // Mise à jour des métadonnées avec les informations du statut
        const statusData = { ...data };
        delete statusData.source;
        delete statusData.status;

        // Ne mettre à jour les métadonnées que si nous ne sommes pas en état déconnecté
        if (!isDisconnected.value) {
          metadata.value = {
            ...metadata.value,
            ...statusData
          };
        }
      }
    } // Dans useAudioStore.js, améliorez la gestion des événements audio_seek
    else if (eventType === 'audio_seek') {
      console.log('Seek event received:', data);

      // Vérifier que les données sont valides et correspondent à la source actuelle
      if (data.position_ms !== undefined && data.source === currentState.value) {
        // MODIFICATION: Toujours considérer que nous sommes connectés lors d'un seek
        isDisconnected.value = false;

        // Mettre à jour la position dans les métadonnées sans effacer les autres infos
        if (metadata.value) {
          // Créer une copie pour maintenir la réactivité
          const updatedMetadata = { ...metadata.value };
          updatedMetadata.position_ms = data.position_ms;

          // IMPORTANT: maintenir l'état is_playing à true lors d'un seek
          updatedMetadata.is_playing = true;

          // Si une durée est fournie, la mettre également à jour
          if (data.duration_ms !== undefined) {
            updatedMetadata.duration_ms = data.duration_ms;
          }

          // Mettre à jour les métadonnées
          metadata.value = updatedMetadata;

          // Récupérer le timestamp de l'événement ou utiliser l'heure actuelle
          const seekTimestamp = data.seek_timestamp || Date.now();

          // Émettre un événement pour le service de progression
          window.dispatchEvent(new CustomEvent('audio-seek', {
            detail: {
              position: data.position_ms,
              timestamp: seekTimestamp,
              source: data.source
            }
          }));

          console.log(`Position mise à jour: ${data.position_ms}ms, timestamp: ${seekTimestamp}`);
        }
      }
    }
  }
  // Fonction pour vérifier l'état de connexion depuis l'API
  async function checkConnectionStatus() {
    try {
      const response = await axios.get('/api/librespot/status');
      const isConnected = response.data.device_connected === true;

      console.log("Vérification de connexion API:", isConnected);

      // Si l'API indique une déconnexion, effacer les métadonnées
      if (!isConnected) {
        clearMetadata();
        lastKnownConnectedState.value = false;
      } else {
        isDisconnected.value = false;

        // Si nous sommes connectés mais sans métadonnées, essayer de les récupérer depuis le statut
        if (isConnected && (!metadata.value || !metadata.value.title) &&
          response.data.raw_status && response.data.raw_status.track) {

          const track = response.data.raw_status.track;

          // Construire des métadonnées valides à partir du statut
          const extractedMetadata = {
            title: track.name,
            artist: track.artist_names?.join(', ') || 'Artiste inconnu',
            album: track.album_name || 'Album inconnu',
            album_art_url: track.album_cover_url,
            duration_ms: track.duration,
            position_ms: track.position,
            is_playing: !response.data.raw_status.paused,
            connected: true,
            deviceConnected: true,
            username: response.data.raw_status.username,
            device_name: response.data.raw_status.device_name
          };

          // Mettre à jour les métadonnées
          updateMetadataFromStatus(extractedMetadata);
        }
      }

      return isConnected;
    } catch (error) {
      console.error("Erreur lors de la vérification de connexion:", error);
      return false;
    }
  }

  return {
    // État
    currentState,
    isTransitioning,
    metadata,
    volume,
    error,
    lastKnownConnectedState,
    lastKnownGoodMetadata,
    isDisconnected,

    // Getters
    isPlaying,
    stateLabel,

    // Actions
    fetchState,
    changeSource,
    updateState,
    handleWebSocketUpdate,
    controlSource,
    hasValidMetadata,
    clearMetadata,
    checkConnectionStatus
  };
});