// frontend/src/composables/usePlaybackProgress.js
import { ref, computed, watch, onUnmounted } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';

export function usePlaybackProgress() {
  const unifiedStore = useUnifiedAudioStore();
  
  const localPosition = ref(0);
  let intervalId = null;
  let isApiSyncing = false;
  
  // Computed properties
  const duration = computed(() => unifiedStore.systemState.metadata?.duration || 0);
  const currentPosition = computed(() => localPosition.value);
  const progressPercentage = computed(() => {
    if (!duration.value || duration.value === 0) return 0;
    return (currentPosition.value / duration.value) * 100;
  });

  // Direct synchronization with store metadata
  watch(() => unifiedStore.systemState.metadata?.position, (newPosition) => {
    if (newPosition !== undefined && !isApiSyncing) {
      localPosition.value = newPosition;
    }
  }, { immediate: true });

  // Local animation while playing
  watch(() => unifiedStore.systemState.metadata?.is_playing, (isPlaying) => {
    stopProgressTimer();
    if (isPlaying) {
      startProgressTimer();
    }
  }, { immediate: true });
  
  function startProgressTimer() {
    if (!intervalId) {
      intervalId = setInterval(() => {
        if (unifiedStore.systemState.metadata?.is_playing && localPosition.value < duration.value) {
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
    
    setTimeout(() => { 
      isApiSyncing = false; 
    }, 200);
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