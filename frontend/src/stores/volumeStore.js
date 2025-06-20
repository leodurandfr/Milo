// frontend/src/stores/volumeStore.js
import { defineStore } from 'pinia';
import { ref } from 'vue';
import axios from 'axios';

export const useVolumeStore = defineStore('volume', () => {
  // État réactif
  const currentVolume = ref(0);
  const isAdjusting = ref(false);
  
  // Référence vers la VolumeBar pour l'affichage
  let volumeBarRef = null;
  
  // Configuration des références
  function setVolumeBarRef(ref) {
    volumeBarRef = ref;
  }
  
  // Actions API
  async function setVolume(volume, showBar = true) {
    if (isAdjusting.value) return false;
    
    try {
      isAdjusting.value = true;
      
      const response = await axios.post('/api/volume/set', {
        volume,
        show_bar: showBar
      });
      
      if (response.data.status === 'success') {
        currentVolume.value = response.data.volume;
        return true;
      }
      return false;
      
    } catch (error) {
      console.error('Error setting volume:', error);
      return false;
    } finally {
      setTimeout(() => {
        isAdjusting.value = false;
      }, 100);
    }
  }
  
  async function adjustVolume(delta, showBar = true) {
    if (isAdjusting.value) return false;
    
    try {
      isAdjusting.value = true;
      
      const response = await axios.post('/api/volume/adjust', {
        delta,
        show_bar: showBar
      });
      
      if (response.data.status === 'success') {
        currentVolume.value = response.data.volume;
        return true;
      }
      return false;
      
    } catch (error) {
      console.error('Error adjusting volume:', error);
      return false;
    } finally {
      setTimeout(() => {
        isAdjusting.value = false;
      }, 100);
    }
  }
  
  async function increaseVolume() {
    return await adjustVolume(5);
  }
  
  async function decreaseVolume() {
    return await adjustVolume(-5);
  }
  
  async function getVolume() {
    try {
      const response = await axios.get('/api/volume/');
      if (response.data.status === 'success') {
        currentVolume.value = response.data.volume;
        return response.data.volume;
      }
    } catch (error) {
      console.error('Error getting volume:', error);
    }
    return currentVolume.value;
  }
  
  // Gestion des événements WebSocket
  function handleVolumeEvent(event) {
    if (event.data && typeof event.data.volume === 'number') {
      currentVolume.value = event.data.volume;
      
      // Afficher la VolumeBar si demandé et référence disponible
      if (event.data.show_bar && volumeBarRef) {
        volumeBarRef.showVolume();
      }
      
      console.log(`Volume updated: ${event.data.volume}% (ALSA: ${event.data.alsa_volume || 'N/A'})`);
    }
  }
  
  return {
    // État
    currentVolume,
    isAdjusting,
    
    // Configuration
    setVolumeBarRef,
    
    // Actions
    setVolume,
    adjustVolume,
    increaseVolume,
    decreaseVolume,
    getVolume,
    
    // WebSocket
    handleVolumeEvent
  };
});