// frontend/src/stores/volume.js
import { defineStore } from 'pinia';
import { ref } from 'vue';
import axios from 'axios';

export const useVolumeStore = defineStore('volume', () => {
  // État de base
  const volume = ref(50);  // Valeur par défaut: 50%
  
  // Actions
  async function setVolume(value) {
    // Assurer que la valeur est dans la plage valide
    const newVolume = Math.max(0, Math.min(100, value));
    
    if (newVolume === volume.value) {
      return true;  // Pas de changement
    }
    
    // Pour l'instant, pas d'API spécifique pour le volume dans le code original
    // On met simplement à jour l'état local
    volume.value = newVolume;
    
    return true;
  }
  
  return {
    // État
    volume,
    
    // Actions
    setVolume
  };
});