/**
 * Gestion optimisée de la progression de lecture
 */
import { ref, computed, watch, onMounted, onUnmounted } from 'vue';
import { useAudioStore } from '@/stores/index';

export function usePlaybackProgress() {
  const audioStore = useAudioStore();
  
  // Variables d'état
  const currentPositionMs = ref(0);
  const progressPercentageValue = ref(0);
  const refreshTimer = ref(null);
  const lastUpdateTime = ref(Date.now());
  const updateInterval = 250; // 250ms - bon compromis entre performance et fluidité
  
  // Exposition des valeurs via computed
  const currentPosition = computed(() => currentPositionMs.value);
  const progressPercentage = computed(() => progressPercentageValue.value);
  
  // Fonction pour mettre à jour la position basée sur le temps réel
  function updatePosition() {
    // Si pas en lecture, ne pas mettre à jour
    if (!audioStore.metadata?.is_playing) return;
    
    // Calculer le temps écoulé depuis la dernière mise à jour
    const now = Date.now();
    const elapsedMs = now - lastUpdateTime.value;
    lastUpdateTime.value = now;
    
    // Mise à jour basée sur le temps réel écoulé
    currentPositionMs.value += elapsedMs;
    
    // Calculer le pourcentage
    const duration = audioStore.metadata?.duration_ms || 0;
    if (duration > 0) {
      progressPercentageValue.value = (currentPositionMs.value / duration) * 100;
      
      // Si on a dépassé la durée, réinitialiser
      if (currentPositionMs.value >= duration) {
        currentPositionMs.value = duration;
      }
    }
  }
  
  // Fonction pour démarrer la simulation
  function startSimulation() {
    // Nettoyage préalable
    stopSimulation();
    
    // Initialiser le temps de référence
    lastUpdateTime.value = Date.now();
    
    // Créer un nouvel intervalle
    refreshTimer.value = setInterval(updatePosition, updateInterval);
  }
  
  // Fonction pour arrêter la simulation
  function stopSimulation() {
    if (refreshTimer.value) {
      clearInterval(refreshTimer.value);
      refreshTimer.value = null;
    }
  }
  
  // Synchroniser depuis les métadonnées
  function syncFromMetadata() {
    if (audioStore.metadata?.position_ms !== undefined) {
      currentPositionMs.value = audioStore.metadata.position_ms;
      
      // Recalculer le pourcentage
      const duration = audioStore.metadata?.duration_ms || 0;
      if (duration > 0) {
        progressPercentageValue.value = (currentPositionMs.value / duration) * 100;
      }
      
      // Réinitialiser le temps de référence
      lastUpdateTime.value = Date.now();
    }
  }
  
  // Surveiller la position dans les métadonnées
  watch(() => audioStore.metadata?.position_ms, (newPosition) => {
    if (newPosition !== undefined) {
      // Vérifier si le changement est significatif (>1s)
      const diff = Math.abs(newPosition - currentPositionMs.value);
      if (diff > 1000) {
        currentPositionMs.value = newPosition;
        lastUpdateTime.value = Date.now();
        
        // Recalculer le pourcentage
        const duration = audioStore.metadata?.duration_ms || 0;
        if (duration > 0) {
          progressPercentageValue.value = (currentPositionMs.value / duration) * 100;
        }
      }
    }
  });
  
  // Surveiller l'état de lecture
  watch(() => audioStore.isPlaying, (isPlaying) => {
    if (isPlaying) {
      syncFromMetadata();
      startSimulation();
    } else {
      stopSimulation();
    }
  });
  
  // Surveiller les changements de piste
  watch(() => audioStore.metadata?.title, (newTitle, oldTitle) => {
    if (newTitle && newTitle !== oldTitle) {
      syncFromMetadata();
      
      // Redémarrer la simulation si lecture en cours
      if (audioStore.isPlaying) {
        startSimulation();
      }
    }
  });
  
  // Surveiller l'état de connexion
  watch(() => audioStore.isDisconnected, (isDisconnected) => {
    if (isDisconnected) {
      stopSimulation();
    } else if (audioStore.isPlaying) {
      syncFromMetadata();
      startSimulation();
    }
  });
  
  // Fonction pour gérer les événements de seek
  function handleSeekEvent(event) {
    const position = event.detail.position;
    if (position !== undefined) {
      currentPositionMs.value = position;
      lastUpdateTime.value = Date.now();
      
      // Recalculer le pourcentage
      const duration = audioStore.metadata?.duration_ms || 0;
      if (duration > 0) {
        progressPercentageValue.value = (currentPositionMs.value / duration) * 100;
      }
    }
  }
  
  // Fonction publique pour effectuer un seek manuel
  function seekTo(position) {
    currentPositionMs.value = position;
    lastUpdateTime.value = Date.now();
    
    // Recalculer le pourcentage
    const duration = audioStore.metadata?.duration_ms || 0;
    if (duration > 0) {
      progressPercentageValue.value = (currentPositionMs.value / duration) * 100;
    }
  }
  
  // Initialisation
  onMounted(() => {
    // Écouter les événements de seek
    window.addEventListener('audio-seek', handleSeekEvent);
    
    // Synchroniser la position initiale
    syncFromMetadata();
    
    // Démarrer la simulation si une piste est en cours de lecture
    if (audioStore.isPlaying) {
      startSimulation();
    }
  });
  
  // Nettoyage
  onUnmounted(() => {
    stopSimulation();
    window.removeEventListener('audio-seek', handleSeekEvent);
  });
  
  // Retourner les valeurs et fonctions nécessaires
  return {
    currentPosition,
    progressPercentage,
    seekTo,
    startProgressTracking: startSimulation,
    stopProgressTracking: stopSimulation
  };
}