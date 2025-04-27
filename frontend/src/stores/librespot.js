// frontend/src/stores/librespot.js
import { defineStore } from 'pinia';
import { computed } from 'vue';
import axios from 'axios';
import { useAudioStore } from './audioStore';

export const useLibrespotStore = defineStore('librespot', () => {
  const audioStore = useAudioStore();
  
  // Getters dérivés du store principal
  const metadata = computed(() => {
    if (audioStore.currentSource === 'librespot') {
      return audioStore.metadata;
    }
    return {};
  });
  
  const isPlaying = computed(() => {
    if (audioStore.currentSource === 'librespot') {
      // Utiliser l'état de lecture des métadonnées
      return audioStore.metadata?.is_playing ?? false;
    }
    return false;
  });
  
  const deviceConnected = computed(() => {
    if (audioStore.currentSource === 'librespot') {
      // Un device est connecté si le plugin est dans l'état CONNECTED et qu'on a des métadonnées
      return audioStore.pluginState === 'connected' && (!!audioStore.metadata?.device_connected || !!audioStore.metadata?.title);
    }
    return false;
  });
  
  const hasTrackInfo = computed(() => {
    return metadata.value?.title && metadata.value?.artist && deviceConnected.value;
  });
  
  // Actions spécifiques à librespot
  async function handleCommand(command, data = {}) {
    try {
      const response = await axios.post('/api/audio/control/librespot', {
        command,
        data
      });
      return response.data.status === 'success';
    } catch (err) {
      console.error(`Erreur commande ${command}:`, err);
      return false;
    }
  }
  
  return {
    // Getters
    metadata,
    isPlaying,
    deviceConnected,
    hasTrackInfo,
    
    // Actions
    handleCommand
  };
});