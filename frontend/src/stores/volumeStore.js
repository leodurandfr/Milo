// frontend/src/stores/volumeStore.js - Version corrigée avec récupération limites au démarrage
import { defineStore } from 'pinia';
import { ref } from 'vue';
import axios from 'axios';

export const useVolumeStore = defineStore('volume', () => {
  // État réactif
  const currentVolume = ref(0);
  const isAdjusting = ref(false);
  
  // Limites de volume dynamiques depuis le backend
  const volumeLimits = ref(null);
  
  // Référence vers la VolumeBar pour l'affichage
  let volumeBarRef = null;
  
  // Configuration des références
  function setVolumeBarRef(ref) {
    volumeBarRef = ref;
  }
  
  // Mettre à jour les limites depuis le backend
  function updateVolumeLimits(limits) {
    if (limits && limits.min !== undefined && limits.max !== undefined) {
      volumeLimits.value = {
        min: limits.min,
        max: limits.max
      };
      console.log(`Volume limits updated: ${limits.min}-${limits.max}%`);
    }
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
  
  // ✅ AJOUT : Récupérer le statut complet avec limites au démarrage
  async function getVolumeStatus() {
    try {
      const response = await axios.get('/api/volume/status');
      if (response.data.status === 'success') {
        const data = response.data.data;
        
        // Mettre à jour le volume
        if (typeof data.volume === 'number') {
          currentVolume.value = data.volume;
        }
        
        // Mettre à jour les limites
        if (data.limits) {
          updateVolumeLimits(data.limits);
        }
        
        console.log('Volume status loaded:', data);
        return data;
      }
    } catch (error) {
      console.error('Error getting volume status:', error);
    }
    return null;
  }
  
  // Gestion des événements WebSocket
  function handleVolumeEvent(event) {
    if (event.data && typeof event.data.volume === 'number') {
      currentVolume.value = event.data.volume;
      
      // Mettre à jour les limites si présentes
      if (event.data.limits) {
        updateVolumeLimits(event.data.limits);
      }
      
      // Afficher la VolumeBar si demandé et référence disponible
      if (event.data.show_bar && volumeBarRef) {
        volumeBarRef.showVolume();
      }
      
      console.log(`Volume updated: ${event.data.volume}% (limits: ${event.data.limits?.min}-${event.data.limits?.max}%)`);
    }
  }
  
  return {
    // État
    currentVolume,
    isAdjusting,
    volumeLimits,
    
    // Configuration
    setVolumeBarRef,
    
    // Actions
    setVolume,
    adjustVolume,
    increaseVolume,
    decreaseVolume,
    getVolume,
    getVolumeStatus, // ✅ AJOUT : Nouvelle méthode pour récupérer statut complet
    
    // WebSocket
    handleVolumeEvent,
    
    // Fonctions utilitaires
    updateVolumeLimits
  };
});