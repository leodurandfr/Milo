// frontend/src/composables/useSourceControl.js
// Generic playback control composable (play/pause, next, previous)
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { logger } from '@/services/logger';

export function useSourceControl(source) {
  const unifiedStore = useUnifiedAudioStore();

  async function executeCommand(command) {
    try {
      await unifiedStore.sendCommand(source, command);
    } catch (error) {
      logger.error('component', `Error executing command ${command} on ${source}`, error);
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

  return {
    togglePlayPause,
    previousTrack,
    nextTrack
  };
}
