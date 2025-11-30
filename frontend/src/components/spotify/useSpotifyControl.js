// frontend/src/components/spotify/useSpotifyControl.js
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { ref } from 'vue';

export function useSpotifyControl() {
  const unifiedStore = useUnifiedAudioStore();
  const isLoading = ref(false);

  async function executeCommand(command) {
    isLoading.value = true;
    try {
      await unifiedStore.sendCommand('spotify', command);
    } catch (error) {
      console.error(`Erreur lors de l'execution de la commande ${command}:`, error);
    } finally {
      setTimeout(() => {
        isLoading.value = false;
      }, 300);
    }
  }

  async function togglePlayPause() {
    const isPlaying = unifiedStore.systemState.metadata?.is_playing;
    await executeCommand(isPlaying ? 'pause' : 'resume');
  }

  async function previousTrack() {
    await executeCommand('prev');
  }

  async function nextTrack() {
    await executeCommand('next');
  }

  async function seekTo(positionMs) {
    await unifiedStore.sendCommand('spotify', 'seek', { position_ms: positionMs });
  }

  return {
    togglePlayPause,
    previousTrack,
    nextTrack,
    seekTo,
    isLoading
  };
}
