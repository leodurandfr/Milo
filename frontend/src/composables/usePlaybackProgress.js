import { ref, computed, watch, onMounted, onUnmounted } from 'vue';
import { useAudioStore } from '@/stores/index';

export function usePlaybackProgress() {
  const audioStore = useAudioStore();
  
  // Variables d'état locales
  const localPosition = ref(0);
  const updateInterval = ref(null);
  
  // Computed properties
  const duration = computed(() => audioStore.metadata?.duration_ms || 0);
  const currentPosition = computed(() => localPosition.value);
  const progressPercentage = computed(() => {
    if (!duration.value) return 0;
    return (localPosition.value / duration.value) * 100;
  });
  
  // Fonction pour mettre à jour la position
  function updatePosition() {
    if (audioStore.isPlaying) {
      localPosition.value += 1000; // Ajouter 1 seconde
      
      // Éviter de dépasser la durée
      if (localPosition.value > duration.value) {
        localPosition.value = duration.value;
      }
    }
  }
  
  // Démarrer/arrêter la mise à jour
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
  
  // Gestion du seek
  function seekTo(position) {
    localPosition.value = position;
    audioStore.controlSource('librespot', 'seek', { position_ms: position });
  }
  
  // Watchers
  watch(() => audioStore.isPlaying, (isPlaying) => {
    if (isPlaying) {
      startTracking();
    } else {
      stopTracking();
    }
  });
  
  // Synchroniser avec les métadonnées
  watch(() => audioStore.metadata?.position_ms, (newPos) => {
    if (newPos !== undefined) {
      localPosition.value = newPos;
    }
  });
  
  // AJOUT : Détecter les changements de piste
  watch(() => audioStore.metadata?.title, (newTitle, oldTitle) => {
    if (newTitle && newTitle !== oldTitle) {
      // Nouvelle piste détectée, réinitialiser la position
      localPosition.value = audioStore.metadata?.position_ms || 0;
    }
  });
  
  // AJOUT : Surveiller aussi l'URI de la piste pour être sûr
  watch(() => audioStore.metadata?.uri, (newUri, oldUri) => {
    if (newUri && newUri !== oldUri) {
      // Nouvelle piste détectée via URI, réinitialiser la position
      localPosition.value = audioStore.metadata?.position_ms || 0;
    }
  });
  
  // Nettoyage
  onMounted(() => {
    if (audioStore.isPlaying) {
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