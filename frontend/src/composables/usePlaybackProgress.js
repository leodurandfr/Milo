import { ref, computed, watch, onUnmounted, onMounted } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';

export function usePlaybackProgress() {
  const unifiedStore = useUnifiedAudioStore();
  
  const localPosition = ref(0);
  let intervalId = null;
  let lastSyncTime = null; // Timestamp de la dernière synchronisation
  
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
      console.log("Position mise à jour depuis le serveur:", newPosition);
      localPosition.value = newPosition;
      lastSyncTime = Date.now(); // Marquer le moment de synchronisation
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
  
  // Resynchronisation automatique quand l'onglet redevient actif
  async function resyncPosition() {
    if (!unifiedStore.metadata?.is_playing || !lastSyncTime) return;
    
    try {
      console.log("Resynchronisation de la position...");
      
      // Appel à l'API pour récupérer la position actuelle
      const response = await fetch('/librespot/status');
      if (response.ok) {
        const data = await response.json();
        const serverPosition = data.metadata?.position;
        
        if (serverPosition !== undefined) {
          const timeDrift = Math.abs(localPosition.value - serverPosition);
          
          // Resynchroniser seulement si la dérive est significative (> 2 secondes)
          if (timeDrift > 2000) {
            console.log(`Resynchronisation nécessaire: drift de ${timeDrift}ms`);
            localPosition.value = serverPosition;
            lastSyncTime = Date.now();
          }
        }
      }
    } catch (error) {
      console.error('Erreur lors de la resynchronisation:', error);
    }
  }
  
  // Gestionnaire pour la visibilité de la page
  function handleVisibilityChange() {
    if (!document.hidden) {
      // L'onglet redevient actif, resynchroniser après un court délai
      setTimeout(resyncPosition, 500);
    }
  }
  
  function seekTo(position) {
    localPosition.value = position;
    lastSyncTime = Date.now();
    unifiedStore.sendCommand('librespot', 'seek', { position_ms: position });
  }
  
  // Configuration des événements lors du montage
  onMounted(() => {
    // Écouter les changements de visibilité de la page
    document.addEventListener('visibilitychange', handleVisibilityChange);
    
    // Également écouter le focus de la fenêtre (fallback)
    window.addEventListener('focus', handleVisibilityChange);
  });
  
  // Nettoyage lors de la destruction du composant
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
    resyncPosition // Exposer pour appel manuel si nécessaire
  };
}