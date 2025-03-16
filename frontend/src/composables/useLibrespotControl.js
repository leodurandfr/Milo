import { useAudioStore } from '@/stores/audio';

export function useLibrespotControl() {
  const audioStore = useAudioStore();

  function togglePlayPause() {
    const isPlaying = audioStore.metadata?.is_playing;
    audioStore.controlSource('librespot', isPlaying ? 'pause' : 'play');
  }

  function previousTrack() {
    audioStore.controlSource('librespot', 'previous');
  }

  function nextTrack() {
    audioStore.controlSource('librespot', 'next');
  }

  return {
    togglePlayPause,
    previousTrack,
    nextTrack
  };
}