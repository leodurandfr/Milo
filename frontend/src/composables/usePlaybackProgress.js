/**
 * Service pour gérer la progression de lecture en temps réel
 * Ce service complète le store audio en calculant localement la position
 * entre les mises à jour WebSocket
 */
import { ref, computed, watch, onMounted, onUnmounted } from 'vue';
import { useAudioStore } from '@/stores/audio';
// Nous n'utiliserons pas directement useWebSocket pour éviter les problèmes de dépendances circulaires

export function usePlaybackProgress() {
  const audioStore = useAudioStore();
  
  // Variables locales pour le suivi de progression
  const playbackStartTime = ref(null);  // Timestamp de début de lecture
  const clientStartPosition = ref(0);   // Position au moment du début de lecture
  const refreshInterval = ref(null);    // Intervalle de rafraîchissement de l'UI
  
  // Position calculée en temps réel
  const currentPosition = computed(() => {
    // Si pas de lecture en cours, utiliser simplement la position du store
    if (!audioStore.isPlaying || !playbackStartTime.value) {
      return audioStore.metadata?.position_ms || 0;
    }
    
    // Si la lecture est en cours, calculer la position en fonction du temps écoulé
    const elapsed = Date.now() - playbackStartTime.value;
    const calculatedPosition = clientStartPosition.value + elapsed;
    
    // Ne pas dépasser la durée totale
    return Math.min(calculatedPosition, audioStore.metadata?.duration_ms || 0);
  });
  
  // Pourcentage de progression pour la barre
  const progressPercentage = computed(() => {
    if (!audioStore.metadata?.duration_ms) return 0;
    return (currentPosition.value / audioStore.metadata.duration_ms) * 100;
  });
  
  // Mettre à jour le suivi quand l'état de lecture change
  watch(() => audioStore.isPlaying, (isPlaying) => {
    if (isPlaying) {
      startProgressTracking();
    } else {
      stopProgressTracking();
    }
  });
  
  // Mettre à jour le suivi quand la position change dans le store
  watch(() => audioStore.metadata?.position_ms, (newPosition) => {
    if (newPosition !== undefined && newPosition !== null) {
      // Mettre à jour la position de référence
      clientStartPosition.value = newPosition;
      
      // Si la lecture est en cours, réinitialiser le timer
      if (audioStore.isPlaying) {
        playbackStartTime.value = Date.now();
      }
    }
  });
  
  // Démarrer le suivi de progression
  function startProgressTracking() {
    console.log('Démarrage du suivi de progression');
    
    // Initialiser le temps de début et la position
    playbackStartTime.value = Date.now();
    clientStartPosition.value = audioStore.metadata?.position_ms || 0;
    
    // Démarrer l'intervalle de rafraîchissement de l'UI (chaque seconde)
    if (!refreshInterval.value) {
      refreshInterval.value = setInterval(() => {
        // Ce setInterval force la réactivité du computed
        // Il ne fait rien d'autre
      }, 1000);
    }
  }
  
  // Arrêter le suivi de progression
  function stopProgressTracking() {
    console.log('Arrêt du suivi de progression');
    
    // Sauvegarder la position actuelle
    const lastPosition = currentPosition.value;
    if (lastPosition) {
      clientStartPosition.value = lastPosition;
    }
    
    // Arrêter l'intervalle
    if (refreshInterval.value) {
      clearInterval(refreshInterval.value);
      refreshInterval.value = null;
    }
    
    // Réinitialiser le temps de début
    playbackStartTime.value = null;
  }
  
  // Réinitialiser le suivi avec une nouvelle position
  function resetProgress(position) {
    clientStartPosition.value = position;
    
    if (audioStore.isPlaying) {
      playbackStartTime.value = Date.now();
    } else {
      playbackStartTime.value = null;
    }
  }
  
  // Forcer une position spécifique (par exemple après un seek)
  function seekTo(position) {
    // Mettre à jour notre position locale
    resetProgress(position);
    
    // Envoyer la commande au backend
    audioStore.controlSource('librespot', 'seek', { position_ms: position });
  }
  
  // Nettoyer l'intervalle lors du démontage du composant
  onUnmounted(() => {
    if (refreshInterval.value) {
      clearInterval(refreshInterval.value);
      refreshInterval.value = null;
    }
  });
  
  // Initialiser le suivi au montage si nécessaire
  onMounted(() => {
    if (audioStore.isPlaying) {
      startProgressTracking();
    }
    
    // Écouter l'événement audio-seek du DOM au lieu d'utiliser le service WebSocket directement
    // Cela évite les problèmes d'importation circulaire
    window.addEventListener('audio-seek', (event) => {
      console.log('Événement audio-seek reçu:', event.detail);
      if (event.detail.position !== undefined) {
        resetProgress(event.detail.position);
      }
    });
  });
  
  return {
    currentPosition,
    progressPercentage,
    seekTo,
    resetProgress,
    startProgressTracking,
    stopProgressTracking
  };
}