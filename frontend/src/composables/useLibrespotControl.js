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
      setTimeout(() => {
        isLoading.value = false;
      }, 300);
    }
  }

  async function togglePlayPause() {
    // Utiliser l'état isPlaying du store principal au lieu de metadata
    const isPlaying = audioStore.isPlaying;
    await executeCommand(isPlaying ? 'pause' : 'resume');
  }

  async function previousTrack() {
    await executeCommand('prev');
  }

  async function nextTrack() {
    await executeCommand('next');
  }

  async function seekTo(positionMs) {
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