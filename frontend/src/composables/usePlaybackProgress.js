/**
 * Version ultra-simplifiée pour simuler la progression de lecture
 */
import { ref, computed, watch, onMounted, onUnmounted } from 'vue';
import { useAudioStore } from '@/stores/audio';

export function usePlaybackProgress() {
  const audioStore = useAudioStore();
  
  // Variables d'état minimales
  const currentPositionMs = ref(0);        // Position actuelle en ms
  const progressPercentageValue = ref(0);  // Pourcentage de progression
  const refreshTimer = ref(null);          // Timer pour la simulation
  
  // Exposition des valeurs via computed pour réactivité
  const currentPosition = computed(() => currentPositionMs.value);
  const progressPercentage = computed(() => progressPercentageValue.value);
  
  // Fonction très simple pour incrémenter la position
  function updatePosition() {
    // Incrémenter la position de 250ms (puisque c'est notre intervalle)
    currentPositionMs.value += 250;
    
    // Calculer le pourcentage
    const duration = audioStore.metadata?.duration_ms || 0;
    if (duration > 0) {
      progressPercentageValue.value = (currentPositionMs.value / duration) * 100;
      
      // Logs pour déboguer
      if (currentPositionMs.value % 5000 < 250) { // Log environ toutes les 5 secondes
        console.log(`Position: ${currentPositionMs.value}ms (${progressPercentageValue.value.toFixed(1)}%)`);
      }
      
      // Si on a dépassé la durée, réinitialiser
      if (currentPositionMs.value >= duration) {
        currentPositionMs.value = duration;
      }
    }
  }
  
  // Fonction pour démarrer la simulation
  function startSimulation() {
    // Nettoyage préalable pour être sûr
    stopSimulation();
    
    console.log("⏱️ Démarrage de la simulation de progression");
    
    // Créer un nouvel intervalle
    refreshTimer.value = setInterval(updatePosition, 250);
  }
  
  // Fonction pour arrêter la simulation
  function stopSimulation() {
    if (refreshTimer.value) {
      console.log("⏹️ Arrêt de la simulation de progression");
      clearInterval(refreshTimer.value);
      refreshTimer.value = null;
    }
  }
  
  // Synchroniser depuis les métadonnées
  function syncFromMetadata() {
    if (audioStore.metadata?.position_ms !== undefined) {
      currentPositionMs.value = audioStore.metadata.position_ms;
      
      // Calculer le pourcentage
      const duration = audioStore.metadata?.duration_ms || 0;
      if (duration > 0) {
        progressPercentageValue.value = (currentPositionMs.value / duration) * 100;
      }
      
      console.log(`⏺️ Position synchronisée: ${currentPositionMs.value}ms`);
    }
  }
  
  // Surveiller la position dans les métadonnées
  watch(() => audioStore.metadata?.position_ms, (newPosition) => {
    if (newPosition !== undefined) {
      console.log(`📌 Nouvelle position reçue: ${newPosition}ms`);
      currentPositionMs.value = newPosition;
      
      // Recalculer le pourcentage
      const duration = audioStore.metadata?.duration_ms || 0;
      if (duration > 0) {
        progressPercentageValue.value = (currentPositionMs.value / duration) * 100;
      }
    }
  });
  
  // Surveiller l'état de lecture
  watch(() => audioStore.metadata?.is_playing, (isPlaying) => {
    if (isPlaying === true) {
      console.log("▶️ Lecture détectée, démarrage de la simulation");
      syncFromMetadata();
      startSimulation();
    } else if (isPlaying === false) {
      console.log("⏸️ Pause détectée, arrêt de la simulation");
      stopSimulation();
    }
  });
  
  // Surveiller les changements de piste
  watch(() => audioStore.metadata?.title, (newTitle) => {
    if (newTitle) {
      console.log(`🎵 Nouvelle piste: "${newTitle}"`);
      syncFromMetadata();
      
      // Redémarrer la simulation si lecture en cours
      if (audioStore.metadata?.is_playing !== false) {
        startSimulation();
      }
    }
  });
  
  // Fonction pour gérer les événements de seek
  function handleSeekEvent(event) {
    const position = event.detail.position_ms;
    if (position !== undefined) {
      console.log(`↪️ Événement seek reçu: ${position}ms`);
      currentPositionMs.value = position;
      
      // Recalculer le pourcentage
      const duration = audioStore.metadata?.duration_ms || 0;
      if (duration > 0) {
        progressPercentageValue.value = (currentPositionMs.value / duration) * 100;
      }
    }
  }
  
  // Fonction publique pour effectuer un seek manuel
  function seekTo(position) {
    console.log(`⏩ Seek manuel à: ${position}ms`);
    currentPositionMs.value = position;
    
    // Recalculer le pourcentage
    const duration = audioStore.metadata?.duration_ms || 0;
    if (duration > 0) {
      progressPercentageValue.value = (currentPositionMs.value / duration) * 100;
    }
  }
  
  // Initialisation
  onMounted(() => {
    console.log("🔄 Montage du composable usePlaybackProgress");
    
    // Écouter les événements de seek
    window.addEventListener('audio-seek', handleSeekEvent);
    
    // Synchroniser la position initiale
    syncFromMetadata();
    
    // Démarrer la simulation si une piste est en cours de lecture
    if (audioStore.metadata?.title && audioStore.metadata?.is_playing !== false) {
      startSimulation();
    }
    
    // Vérification périodique que la simulation fonctionne
    const healthCheck = setInterval(() => {
      // Si on a une piste en cours, la lecture n'est pas en pause,
      // mais la simulation n'est pas active
      if (audioStore.metadata?.title && 
          audioStore.metadata?.is_playing !== false && 
          !refreshTimer.value) {
        console.warn("🔄 La simulation devrait être active mais ne l'est pas, redémarrage...");
        syncFromMetadata();
        startSimulation();
      }
    }, 5000);
    
    // Nettoyer la vérification au démontage
    onUnmounted(() => {
      clearInterval(healthCheck);
    });
  });
  
  // Nettoyage
  onUnmounted(() => {
    console.log("❌ Démontage du composable usePlaybackProgress");
    stopSimulation();
    window.removeEventListener('audio-seek', handleSeekEvent);
  });
  
  // Retourner les valeurs et fonctions nécessaires
  return {
    currentPosition,         // Position actuelle en ms
    progressPercentage,      // Pourcentage de progression
    seekTo,                  // Fonction pour seek manuel
    
    // Pour compatibilité avec l'API précédente
    startProgressTracking: startSimulation,
    stopProgressTracking: stopSimulation
  };
}