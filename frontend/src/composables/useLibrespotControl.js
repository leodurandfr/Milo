import { useLibrespotStore } from '@/stores/librespot';
import { ref } from 'vue';

export function useLibrespotControl() {
  const librespotStore = useLibrespotStore();
  const isLoading = ref(false);

  async function executeCommand(command) {
    isLoading.value = true;
    try {
      await librespotStore.handleCommand(command);
    } catch (error) {
      console.error(`Erreur lors de l'exécution de la commande ${command}:`, error);
    } finally {
      setTimeout(() => {
        isLoading.value = false;
      }, 300);
    }
  }

  async function togglePlayPause() {
    const isPlaying = librespotStore.isPlaying;
    await executeCommand(isPlaying ? 'pause' : 'resume');
  }

  async function previousTrack() {
    await executeCommand('prev');
  }

  async function nextTrack() {
    await executeCommand('next');
  }

  async function seekTo(positionMs) {
    await librespotStore.handleCommand('seek', { position_ms: positionMs });
  }

  return {
    togglePlayPause,
    previousTrack,
    nextTrack,
    seekTo,
    isLoading
  };
}