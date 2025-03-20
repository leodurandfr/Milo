// frontend/src/stores/state.js
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';

export const useStateStore = defineStore('audioState', () => {
  // État de base
  const currentState = ref('none');
  const isTransitioning = ref(false);
  
  // Getters
  const validStates = computed(() => [
    'none',           // Aucune source active
    'librespot',      // Spotify Connect
    'bluetooth',      // Bluetooth Audio
    'macos',          // MacOS via Snapclient
    'webradio'        // Web Radio
  ]);
  
  // Mettre à jour l'état du système
  function updateState(newState, transitioning = false) {
    if (validStates.value.includes(newState)) {
      currentState.value = newState;
      isTransitioning.value = transitioning;
    } else {
      console.error(`État invalide: ${newState}`);
    }
  }
  
  // Marquer le début d'une transition
  function startTransition() {
    isTransitioning.value = true;
  }
  
  // Marquer la fin d'une transition
  function endTransition() {
    isTransitioning.value = false;
  }
  
  return {
    // État
    currentState,
    isTransitioning,
    
    // Getters
    validStates,
    
    // Actions
    updateState,
    startTransition,
    endTransition
  };
});