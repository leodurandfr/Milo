import { ref, computed, watch, onMounted, onUnmounted } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';

export function usePlaybackProgress() {
  const unifiedStore = useUnifiedAudioStore();
  
  const localPosition = ref(0);
  const updateInterval = ref(null);
  const lastUpdateTime = ref(Date.now());
  
  const duration = computed(() => unifiedStore.metadata?.duration || 0);
  const currentPosition = computed(() => localPosition.value);
  const progressPercentage = computed(() => {
    if (!duration.value) return 0;
    return (localPosition.value / duration.value) * 100;
  });
  
  function updatePosition() {
    if (unifiedStore.metadata?.is_playing) {
      const now = Date.now();
      const elapsed = now - lastUpdateTime.value;
      localPosition.value += elapsed;
      lastUpdateTime.value = now;
      
      if (localPosition.value > duration.value) {
        localPosition.value = duration.value;
      }
    }
  }
  
  function startTracking() {
    if (!updateInterval.value) {
      lastUpdateTime.value = Date.now();
      updateInterval.value = setInterval(updatePosition, 100);
    }
  }
  
  function stopTracking() {
    if (updateInterval.value) {
      clearInterval(updateInterval.value);
      updateInterval.value = null;
    }
  }
  
  function seekTo(position) {
    localPosition.value = position;
    lastUpdateTime.value = Date.now();
    unifiedStore.sendCommand('librespot', 'seek', { position_ms: position });
  }
  
  function initializePosition(position) {
    localPosition.value = position;
    lastUpdateTime.value = Date.now();
  }
  
  watch(() => unifiedStore.metadata?.is_playing, (isPlaying) => {
    if (isPlaying) {
      startTracking();
    } else {
      stopTracking();
    }
  });
  
  watch(() => unifiedStore.metadata?.position, (newPos) => {
    if (newPos !== undefined && Math.abs(newPos - localPosition.value) > 1000) {
      localPosition.value = newPos;
      lastUpdateTime.value = Date.now();
    }
  });
  
  watch(() => unifiedStore.metadata?.uri, (newUri, oldUri) => {
    if (newUri && newUri !== oldUri) {
      localPosition.value = unifiedStore.metadata?.position || 0;
      lastUpdateTime.value = Date.now();
    }
  });
  
  onMounted(() => {
    if (unifiedStore.metadata?.position !== undefined) {
      localPosition.value = unifiedStore.metadata.position;
      lastUpdateTime.value = Date.now();
    }
    
    if (unifiedStore.metadata?.is_playing) {
      startTracking();
    }
  });
  
  onUnmounted(() => {
    stopTracking();
  });
  
  return {
    currentPosition,
    duration,
    progressPercentage,
    seekTo,
    initializePosition
  };
}