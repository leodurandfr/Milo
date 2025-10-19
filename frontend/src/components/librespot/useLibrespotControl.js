// frontend/src/composables/useLibrespotControl.js
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { ref } from 'vue';

export function useLibrespotControl() {
  const unifiedStore = useUnifiedAudioStore();
  const isLoading = ref(false);

  async function executeCommand(command) {
    isLoading.value = true;
    try {
      await unifiedStore.sendCommand('librespot', command);
    } catch (error) {
      console.error(`Erreur lors de l'exécution de la commande ${command}:`, error);
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
    await unifiedStore.sendCommand('librespot', 'seek', { position_ms: positionMs });
  }

  return {
    togglePlayPause,
    previousTrack,
    nextTrack,
    seekTo,
    isLoading
  };
}