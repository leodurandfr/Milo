import { ref, computed, watch, onUnmounted, onMounted } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';

export function usePlaybackProgress() {
  const unifiedStore = useUnifiedAudioStore();
  
  const localPosition = ref(0);
  let intervalId = null;
  let isApiSyncing = false;
  
  // Durée de la musique
  const duration = computed(() => unifiedStore.metadata?.duration || 0);
  
  // Position affichée (animation locale)
  const currentPosition = computed(() => localPosition.value);
  
  // Pourcentage de progression
  const progressPercentage = computed(() => {
    if (!duration.value || duration.value === 0) return 0;
    return (currentPosition.value / duration.value) * 100;
  });
  
  // ✅ SOLUTION SIMPLE : Protection contre les resets + synchronisation
  watch(() => unifiedStore.metadata?.position, (newPosition) => {
    if (newPosition !== undefined && !isApiSyncing) {
      // Protection simple contre les resets intempestifs
      if (newPosition === 0 && localPosition.value > 5000) {
        console.warn("Position 0 ignorée (probable reset)");
        return;
      }
      
      localPosition.value = newPosition;
    }
  }, { immediate: true });
  
  // Animation locale quand la musique joue
  watch(() => unifiedStore.metadata?.is_playing, (isPlaying) => {
    stopProgressTimer();
    
    if (isPlaying) {
      startProgressTimer();
    }
  }, { immediate: true });
  
  function startProgressTimer() {
    if (!intervalId) {
      intervalId = setInterval(() => {
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
  
  // ✅ SOLUTION SIMPLE : Resynchronisation via store unifié
  async function resyncCompleteState() {
    if (!unifiedStore.metadata?.is_playing) return;
    
    try {
      isApiSyncing = true;
      
      // Forcer le refresh des métadonnées via le store
      await unifiedStore.sendCommand('librespot', 'refresh_metadata', {});
      
    } catch (error) {
      console.error('Erreur lors de la resynchronisation:', error);
    } finally {
      setTimeout(() => {
        isApiSyncing = false;
      }, 500);
    }
  }
  
  // Gestionnaire pour la visibilité de la page
  function handleVisibilityChange() {
    if (!document.hidden) {
      // L'onglet redevient actif, resynchroniser
      setTimeout(resyncCompleteState, 300);
    }
  }
  
  function seekTo(position) {
    isApiSyncing = true;
    localPosition.value = position;
    
    unifiedStore.sendCommand('librespot', 'seek', { position_ms: position });
    
    setTimeout(() => {
      isApiSyncing = false;
    }, 200);
  }
  
  // Configuration des événements
  onMounted(() => {
    document.addEventListener('visibilitychange', handleVisibilityChange);
    window.addEventListener('focus', handleVisibilityChange);
  });
  
  // Nettoyage
  onUnmounted(() => {
    stopProgressTimer();
    document.removeEventListener('visibilitychange', handleVisibilityChange);
    window.removeEventListener('focus', handleVisibilityChange);
  });
  
  return {
    currentPosition,
    duration,
    progressPercentage,
    seekTo,
    resyncCompleteState
  };
}