import { ref, computed, watch, onMounted, onUnmounted } from 'vue';
import { useLibrespotStore } from '@/stores/librespot';

export function usePlaybackProgress() {
  const librespotStore = useLibrespotStore();
  
  // Variables d'état locales
  const localPosition = ref(0);
  const updateInterval = ref(null);
  
  // Computed properties
  const duration = computed(() => librespotStore.metadata?.duration_ms || 0);
  const currentPosition = computed(() => localPosition.value);
  const progressPercentage = computed(() => {
    if (!duration.value) return 0;
    return (localPosition.value / duration.value) * 100;
  });
  
  // Fonction pour mettre à jour la position
  function updatePosition() {
    if (librespotStore.isPlaying) {
      localPosition.value += 1000;
      
      // Éviter de dépasser la durée
      if (localPosition.value > duration.value) {
        localPosition.value = duration.value;
      }
    }
  }
  
  function startTracking() {
    if (!updateInterval.value) {
      updateInterval.value = setInterval(updatePosition, 1000);
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
    librespotStore.handleCommand('seek', { position_ms: position });
  }
  
  // Watchers
  watch(() => librespotStore.isPlaying, (isPlaying) => {
    if (isPlaying) {
      startTracking();
    } else {
      stopTracking();
    }
  });
  
  watch(() => librespotStore.metadata?.position_ms, (newPos) => {
    if (newPos !== undefined) {
      localPosition.value = newPos;
    }
  });
  
  watch(() => librespotStore.metadata?.title, (newTitle, oldTitle) => {
    if (newTitle && newTitle !== oldTitle) {
      localPosition.value = librespotStore.metadata?.position_ms || 0;
    }
  });
  
  watch(() => librespotStore.metadata?.uri, (newUri, oldUri) => {
    if (newUri && newUri !== oldUri) {
      localPosition.value = librespotStore.metadata?.position_ms || 0;
    }
  });
  
  onMounted(() => {
    if (librespotStore.isPlaying) {
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
    seekTo
  };
}