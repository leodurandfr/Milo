import { ref, computed, watch, onUnmounted } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';

export function usePlaybackProgress() {
  const unifiedStore = useUnifiedAudioStore();
  
  const localPosition = ref(0);
  let intervalId = null;
  
  // Durée de la musique
  const duration = computed(() => unifiedStore.metadata?.duration || 0);
  
  // Position affichée (animation locale)
  const currentPosition = computed(() => localPosition.value);
  
  // Pourcentage de progression
  const progressPercentage = computed(() => {
    if (!duration.value || duration.value === 0) return 0;
    return (currentPosition.value / duration.value) * 100;
  });
  
  // Synchronisation avec la position de l'API
  watch(() => unifiedStore.metadata?.position, (newPosition) => {
    if (newPosition !== undefined) {
      localPosition.value = newPosition;
    }
  }, { immediate: true });
  
  // Animation locale quand la musique joue
  watch(() => unifiedStore.metadata?.is_playing, (isPlaying) => {
    // Arrêter d'abord toute animation en cours
    stopProgressTimer();
    
    if (isPlaying) {
      startProgressTimer();
    }
  }, { immediate: true });
  
  function startProgressTimer() {
    if (!intervalId) {
      intervalId = setInterval(() => {
        // Vérifier que la musique joue toujours avant d'incrémenter
        if (unifiedStore.metadata?.is_playing && localPosition.value < duration.value) {
          localPosition.value += 100; // Ajouter 100ms
        }
      }, 100);
    }
  }
  
  function stopProgressTimer() {
    if (intervalId) {
      clearInterval(intervalId);
      intervalId = null;
    }
  }
  
  function seekTo(position) {
    localPosition.value = position;
    unifiedStore.sendCommand('librespot', 'seek', { position_ms: position });
  }
  
  // SUPPRESSION de initializePosition - plus nécessaire
  
  // Nettoyage lors de la destruction du composant
  onUnmounted(() => {
    stopProgressTimer();
  });
  
  return {
    currentPosition,
    duration,
    progressPercentage,
    seekTo
  };
}