// frontend/src/composables/usePlaybackProgress.js
import { ref, computed, watch, onMounted, onUnmounted } from 'vue';
import { useLibrespotStore } from '@/stores/librespot';
import { useAudioStore } from '@/stores/audioStore';

export function usePlaybackProgress() {
  const librespotStore = useLibrespotStore();
  const audioStore = useAudioStore();
  
  // Variables d'état locales
  const localPosition = ref(0);
  const updateInterval = ref(null);
  
  // Computed properties
  const duration = computed(() => audioStore.metadata?.duration || 0);
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
  
  // Synchroniser avec la position des métadonnées
  watch(() => audioStore.metadata?.position, (newPos) => {
    if (newPos !== undefined) {
      localPosition.value = newPos;
    }
  });
  
  // Réinitialiser sur changement de piste (avec URI)
  watch(() => audioStore.metadata?.uri, (newUri, oldUri) => {
    if (newUri && newUri !== oldUri) {
      localPosition.value = audioStore.metadata?.position || 0;
    }
  });
  
  onMounted(() => {
    // Initialiser la position
    if (audioStore.metadata?.position !== undefined) {
      localPosition.value = audioStore.metadata.position;
    }
    
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