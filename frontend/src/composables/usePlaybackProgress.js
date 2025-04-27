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
  const lastUpdateTime = ref(Date.now()); // Pour gérer la dérive temporelle
  
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
      const now = Date.now();
      const elapsed = now - lastUpdateTime.value;
      localPosition.value += elapsed;
      lastUpdateTime.value = now;
      
      // Éviter de dépasser la durée
      if (localPosition.value > duration.value) {
        localPosition.value = duration.value;
      }
    }
  }
  
  function startTracking() {
    if (!updateInterval.value) {
      lastUpdateTime.value = Date.now();
      updateInterval.value = setInterval(updatePosition, 100); // Plus précis à 100ms
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
    librespotStore.handleCommand('seek', { position_ms: position });
  }
  
  function initializePosition(position) {
    localPosition.value = position;
    lastUpdateTime.value = Date.now();
  }
  
  // Watchers
  watch(() => librespotStore.isPlaying, (isPlaying) => {
    if (isPlaying) {
      startTracking();
    } else {
      stopTracking();
    }
  });
  
  // Synchroniser avec la position des métadonnées (inclus les events seek)
  watch(() => audioStore.metadata?.position, (newPos) => {
    if (newPos !== undefined && Math.abs(newPos - localPosition.value) > 1000) {
      // Si la différence est significative (> 1s), mettre à jour
      localPosition.value = newPos;
      lastUpdateTime.value = Date.now();
    }
  });
  
  // Réinitialiser sur changement de piste (avec URI)
  watch(() => audioStore.metadata?.uri, (newUri, oldUri) => {
    if (newUri && newUri !== oldUri) {
      localPosition.value = audioStore.metadata?.position || 0;
      lastUpdateTime.value = Date.now();
    }
  });
  
  onMounted(() => {
    // Initialiser la position au montage
    if (audioStore.metadata?.position !== undefined) {
      localPosition.value = audioStore.metadata.position;
      lastUpdateTime.value = Date.now();
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
    seekTo,
    initializePosition
  };
}