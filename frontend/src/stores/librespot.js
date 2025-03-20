// frontend/src/stores/librespot.js
import { defineStore } from 'pinia';
import { ref, computed, watch } from 'vue';
import axios from 'axios';

export const useLibrespotStore = defineStore('librespot', () => {
  // État
  const metadata = ref({});
  const isActuallyConnected = ref(false);
  const connectionLastChecked = ref(Date.now());
  const manualConnectionStatus = ref(null);
  const lastKnownGoodMetadata = ref({});
  const lastMessages = ref([]);
  const statusResult = ref(null);
  const connectionTimeoutMs = 20000;
  
  // Getters
  const isPlaying = computed(() => metadata.value?.is_playing === true);
  const deviceConnected = computed(() => !isDisconnected.value);
  const isDisconnected = computed(() => {
    // Priorité au statut manuel s'il est défini
    if (manualConnectionStatus.value !== null) {
      return !manualConnectionStatus.value;
    }
    return !isActuallyConnected.value;
  });
  
  const hasTrackInfo = computed(() => {
    const hasCurrent = metadata.value &&
      metadata.value.title &&
      metadata.value.artist;
      
    const isConnectedWithoutMetadata = !isDisconnected.value &&
      statusResult.value &&
      statusResult.value.device_connected === true &&
      !hasCurrent;
      
    if (isConnectedWithoutMetadata &&
      statusResult.value?.raw_status?.track?.name) {
      
      const track = statusResult.value.raw_status.track;
      
      updateMetadata({
        title: track.name,
        artist: track.artist_names?.join(', ') || 'Artiste inconnu',
        album: track.album_name || 'Album inconnu',
        album_art_url: track.album_cover_url,
        duration_ms: track.duration,
        position_ms: track.position,
        is_playing: !statusResult.value.raw_status.paused,
        connected: true,
        deviceConnected: true,
        username: statusResult.value.raw_status.username,
        device_name: statusResult.value.raw_status.device_name
      });
      
      return true;
    }
    
    return hasCurrent && !isDisconnected.value;
  });
  
  // Actions
  async function handleCommand(command, data = {}) {
    try {
      console.log(`Exécution de la commande librespot: ${command}`, data);
      
      // Envoyer la commande à l'API
      const response = await axios.post(`/api/audio/control/librespot`, {
        command,
        data
      });
      
      if (response.data.status === 'error') {
        throw new Error(response.data.message);
      }
      
      return true;
    } catch (error) {
      console.error(`Erreur lors de l'exécution de la commande ${command}:`, error);
      return false;
    }
  }
  
  async function checkConnectionStatus() {
    try {
      const response = await axios.get('/api/librespot/status');
      const isConnected = response.data.device_connected === true;
      
      console.log("Vérification de connexion API:", isConnected);
      
      // Si l'API indique une déconnexion, effacer les métadonnées
      if (!isConnected) {
        clearMetadata();
      } else {
        isActuallyConnected.value = true;
        
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
          updateMetadata(extractedMetadata);
        }
      }
      
      connectionLastChecked.value = Date.now();
      statusResult.value = response.data;
      return isConnected;
    } catch (error) {
      console.error("Erreur lors de la vérification de connexion:", error);
      return false;
    }
  }
  
  function handleEvent(eventType, data) {
    if (eventType === 'audio_metadata_updated') {
      console.log('Metadata update received:', data.metadata);
      if (data.metadata) {
        // Si nous recevons des métadonnées, nous ne sommes probablement pas déconnectés
        isActuallyConnected.value = true;
        
        if (Object.keys(data.metadata).length > 0) {
          // Fusionner avec les métadonnées existantes
          const mergedMetadata = {
            ...metadata.value,  // Garder les anciennes métadonnées
            ...data.metadata    // Ajouter les nouvelles
          };
          
          // Vérifier si la mise à jour est significative
          if (hasSignificantChanges(mergedMetadata, metadata.value)) {
            // Si nous avons des informations valides, les stocker
            if (hasValidMetadata(mergedMetadata)) {
              lastKnownGoodMetadata.value = { ...mergedMetadata };
            }
            
            // Mettre à jour les métadonnées
            metadata.value = mergedMetadata;
            console.log('Mise à jour significative des métadonnées appliquée');
          } else {
            console.log('Mise à jour des métadonnées ignorée (changements non significatifs)');
          }
        }
      }
    } 
    else if (eventType === 'audio_status_updated') {
      console.log('Status update received:', data);
      
      // *** DÉCONNEXION EXPLICITE ***
      if (data.status === 'disconnected' || data.connected === false) {
        console.log("Déconnexion explicite détectée, effacement des métadonnées");
        clearMetadata();
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
        isActuallyConnected.value = true;
        
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
    }
    else if (eventType === 'audio_seek') {
      console.log('Seek event received:', data);
      
      if (data.position_ms !== undefined) {
        // MODIFICATION: Toujours considérer que nous sommes connectés lors d'un seek
        isActuallyConnected.value = true;
        
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
    
    // Ajouter aux messages de debug
    addDebugMessage(eventType, data);
  }
  
  function updateMetadata(newMetadata) {
    // Vérifier que les données sont valides
    if (!newMetadata || !Object.keys(newMetadata).length) {
      console.warn("Tentative de mise à jour des métadonnées avec des données invalides");
      return;
    }
    
    console.log("Mise à jour des métadonnées:", newMetadata);
    
    // Mettre à jour les métadonnées
    metadata.value = newMetadata;
    
    // Mise à jour de l'état de connexion
    if (newMetadata.connected !== undefined) {
      isActuallyConnected.value = newMetadata.connected;
    }
    
    // Sauvegarder comme dernières métadonnées valides
    if (hasValidMetadata(newMetadata)) {
      lastKnownGoodMetadata.value = { ...newMetadata };
    }
  }
  
  function clearMetadata() {
    console.log("Effacement des métadonnées suite à déconnexion");
    metadata.value = {};
    isActuallyConnected.value = false;
  }
  
  // Utilitaires
  function hasValidMetadata(meta) {
    return meta && (
      meta.title ||
      meta.artist ||
      meta.album_art_url ||
      meta.duration_ms
    );
  }
  
  function hasSignificantChanges(newMeta, oldMeta) {
    // Si pas d'anciennes métadonnées ou d'identifiants différents, toujours mettre à jour
    if (!oldMeta || !newMeta) return true;
    
    // Vérifier les changements critiques qui nécessitent une mise à jour
    const criticalChanges = [
      // Changement de piste
      newMeta.title !== oldMeta.title || 
      newMeta.artist !== oldMeta.artist,
      
      // Changement d'état de lecture
      newMeta.is_playing !== oldMeta.is_playing,
      
      // Changement d'état de connexion
      newMeta.connected !== oldMeta.connected || 
      newMeta.deviceConnected !== oldMeta.deviceConnected,
      
      // Changement d'artwork (seulement si les deux valeurs existent)
      newMeta.album_art_url && oldMeta.album_art_url && 
      newMeta.album_art_url !== oldMeta.album_art_url
    ];
    
    // Ignorer les mises à jour de position trop fréquentes (moins de 1 seconde)
    const positionChangeSignificant = 
      !newMeta.position_ms || 
      !oldMeta.position_ms || 
      Math.abs(newMeta.position_ms - oldMeta.position_ms) > 1000;
    
    // Retourner true si au moins un changement critique est détecté
    return criticalChanges.some(change => change) || positionChangeSignificant;
  }
  
  function addDebugMessage(type, data) {
    const now = new Date();
    const timeString = now.toLocaleTimeString();
    
    lastMessages.value.unshift({
      timestamp: timeString,
      type: type,
      data: data
    });
    
    if (lastMessages.value.length > 5) {
      lastMessages.value.pop();
    }
  }
  
  // Exposer fonctions de debug pour le DebugPanel
  function toggleConnectionStatus() {
    if (manualConnectionStatus.value === null) {
      manualConnectionStatus.value = !deviceConnected.value;
    } else {
      manualConnectionStatus.value = !manualConnectionStatus.value;
    }
    console.log("État de connexion forcé à:", manualConnectionStatus.value);
  }
  
  function resetConnectionStatus() {
    manualConnectionStatus.value = null;
    console.log("État de connexion réinitialisé (autodétection)");
  }
  
  // Suivi des changements d'état de connexion
  watch(() => [
    metadata.value?.deviceConnected,
    metadata.value?.connected,
    metadata.value?.is_playing
  ],
  (newValues, oldValues) => {
    if (!newValues || !oldValues) return;
    
    const [newDeviceConnected, newConnected, newIsPlaying] = newValues || [false, false, false];
    
    if (isDisconnected.value) {
      isActuallyConnected.value = false;
      return;
    }
    
    if (newDeviceConnected || newConnected || newIsPlaying) {
      if (Object.keys(metadata.value).length > 0 && !isDisconnected.value) {
        isActuallyConnected.value = true;
        connectionLastChecked.value = Date.now();
      } else {
        checkConnectionStatus();
      }
    }
    
    if (isActuallyConnected.value &&
      !newDeviceConnected && !newConnected && !newIsPlaying) {
      checkConnectionStatus();
    }
    
    if (manualConnectionStatus.value !== null) {
      isActuallyConnected.value = manualConnectionStatus.value;
    }
  });
  
  return {
    // État
    metadata,
    isActuallyConnected,
    connectionLastChecked,
    manualConnectionStatus,
    lastKnownGoodMetadata,
    lastMessages,
    statusResult,
    
    // Getters
    isPlaying,
    deviceConnected,
    isDisconnected,
    hasTrackInfo,
    
    // Actions
    handleCommand,
    checkConnectionStatus,
    handleEvent,
    updateMetadata,
    clearMetadata,
    
    // Utilitaires et debug
    hasValidMetadata,
    hasSignificantChanges,
    addDebugMessage,
    toggleConnectionStatus,
    resetConnectionStatus
  };
});