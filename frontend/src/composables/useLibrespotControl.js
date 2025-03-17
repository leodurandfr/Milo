import { useAudioStore } from '@/stores/audio';
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
    await executeCommand(isPlaying ? 'pause' : 'play');
  }

  async function previousTrack() {
    console.log('previousTrack appelé');
    await executeCommand('previous');
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