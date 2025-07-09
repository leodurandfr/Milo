// frontend/src/composables/usePlaybackProgress.js - Version OPTIM nettoyée
import { ref, computed, watch, onUnmounted } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';

export function usePlaybackProgress() {
  const unifiedStore = useUnifiedAudioStore();
  
  const localPosition = ref(0);
  let intervalId = null;
  let isApiSyncing = false;
  
  // Computed properties
  const duration = computed(() => unifiedStore.metadata?.duration || 0);
  const currentPosition = computed(() => localPosition.value);
  const progressPercentage = computed(() => {
    if (!duration.value || duration.value === 0) return 0;
    return (currentPosition.value / duration.value) * 100;
  });
  
  // Synchronisation avec les métadonnées du store
  watch(() => unifiedStore.metadata?.position, (newPosition) => {
    if (newPosition !== undefined && !isApiSyncing) {
      // Protection contre les resets intempestifs
      if (newPosition === 0 && localPosition.value > 5000) {
        console.warn("Position 0 ignorée (probable reset)");
        return;
      }
      localPosition.value = newPosition;
    }
  }, { immediate: true });
  
  // Animation locale pendant la lecture
  watch(() => unifiedStore.metadata?.is_playing, (isPlaying) => {
    stopProgressTimer();
    if (isPlaying) startProgressTimer();
  }, { immediate: true });
  
  function startProgressTimer() {
    if (!intervalId) {
      intervalId = setInterval(() => {
        if (unifiedStore.metadata?.is_playing && localPosition.value < duration.value) {
          localPosition.value += 100;
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
    isApiSyncing = true;
    localPosition.value = position;
    unifiedStore.sendCommand('librespot', 'seek', { position_ms: position });
    setTimeout(() => { isApiSyncing = false; }, 200);
  }
  
  // Cleanup
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