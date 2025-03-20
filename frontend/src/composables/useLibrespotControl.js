import { useAudioStore } from '@/stores/index';
import { ref } from 'vue';

export function useLibrespotControl() {
  const audioStore = useAudioStore();
  const isLoading = ref(false);

  async function executeCommand(command) {
    isLoading.value = true;
    try {
      await audioStore.controlSource('librespot', command);
    } catch (error) {
      console.error(`Erreur lors de l'exécution de la commande ${command}:`, error);
    } finally {
      // Reset loading state after a short delay to show feedback
      setTimeout(() => {
        isLoading.value = false;
      }, 300);
    }
  }

  async function togglePlayPause() {
    console.log('togglePlayPause appelé');
    const isPlaying = audioStore.metadata?.is_playing;
    
    // Utiliser 'resume' au lieu de 'play' quand l'état est 'paused'
    // Selon la doc go-librespot, 'play' est pour démarrer un nouveau contenu
    // tandis que 'resume' est pour reprendre la lecture après une pause
    await executeCommand(isPlaying ? 'pause' : 'resume');
    
    // Alternative: on peut aussi utiliser 'playpause' qui fait le toggle automatiquement
    // await executeCommand('playpause');
  }

  async function previousTrack() {
    console.log('previousTrack appelé');
    // Mettre à jour pour utiliser 'prev' au lieu de 'previous'
    // conformément à l'API go-librespot
    await executeCommand('prev');
  }

  async function nextTrack() {
    console.log('nextTrack appelé');
    await executeCommand('next');
  }

  async function seekTo(positionMs) {
    console.log(`seekTo appelé avec position: ${positionMs}ms`);
    await audioStore.controlSource('librespot', 'seek', { position_ms: positionMs });
  }

  return {
    togglePlayPause,
    previousTrack,
    nextTrack,
    seekTo,
    isLoading
  };
}