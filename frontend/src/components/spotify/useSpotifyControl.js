// frontend/src/components/spotify/useSpotifyControl.js
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';

export function useSpotifyControl() {
  const unifiedStore = useUnifiedAudioStore();

  async function executeCommand(command) {
    try {
      await unifiedStore.sendCommand('spotify', command);
    } catch (error) {
      console.error(`Error executing command ${command}:`, error);
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
