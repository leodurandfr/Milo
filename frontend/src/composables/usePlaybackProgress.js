import { ref, computed, watch, onMounted, onUnmounted } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';

export function usePlaybackProgress() {
  const unifiedStore = useUnifiedAudioStore();
  
  const basePosition = ref(0);
  const baseTimestamp = ref(0);
  const ticker = ref(0);  // Déclencheur réactif
  let intervalId = null;
  
  // Position calculée basée sur le temps écoulé
  const currentPosition = computed(() => {
    // Le ticker force la réévaluation périodique
    ticker.value; 
    
    if (!unifiedStore.metadata?.is_playing) return basePosition.value;
    
    const now = Date.now();
    const elapsed = now - baseTimestamp.value;
    return Math.min(basePosition.value + elapsed, unifiedStore.metadata?.duration || 0);
  });
  
  const duration = computed(() => unifiedStore.metadata?.duration || 0);
  
  const progressPercentage = computed(() => {
    if (!duration.value) return 0;
    return (currentPosition.value / duration.value) * 100;
  });
  
  // Synchronisation avec les métadonnées du store
  watch(() => unifiedStore.metadata, (newMetadata) => {
    if (newMetadata?.position !== undefined && newMetadata?.timestamp) {
      basePosition.value = newMetadata.position;
      baseTimestamp.value = newMetadata.timestamp * 1000; // Convertir en millisecondes
    }
  }, { deep: true });
  
  // Gestion de la mise à jour périodique
  watch(() => unifiedStore.metadata?.is_playing, (isPlaying) => {
    if (isPlaying) {
      startTicker();
    } else {
      stopTicker();
    }
  }, { immediate: true });
  
  function startTicker() {
    if (!intervalId) {
      intervalId = setInterval(() => {
        ticker.value++;  // Force la mise à jour des computed
      }, 100);  // Mise à jour toutes les 100ms
    }
  }
  
  function stopTicker() {
    if (intervalId) {
      clearInterval(intervalId);
      intervalId = null;
    }
  }
  
  function seekTo(position) {
    basePosition.value = position;
    baseTimestamp.value = Date.now();
    unifiedStore.sendCommand('librespot', 'seek', { position_ms: position });
  }
  
  function initializePosition(position, timestamp) {
    basePosition.value = position;
    baseTimestamp.value = timestamp ? timestamp * 1000 : Date.now();
  }
  
  // Nettoyage lors de la destruction du composant
  onUnmounted(() => {
    stopTicker();
  });
  
  return {
    currentPosition,
    duration,
    progressPercentage,
    seekTo,
    initializePosition
  };
}