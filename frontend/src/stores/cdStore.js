// frontend/src/stores/cdStore.js
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';

export const useCdStore = defineStore('cd', () => {
  const unifiedStore = useUnifiedAudioStore();

  // All disc + playback state is derived from the central audio mirror
  // (unifiedAudioStore.systemState), the single source of truth. The disc
  // lives in `details` from the moment it is read, session or not, so deriving
  // here — rather than maintaining delta-fed refs — keeps the store in sync
  // across source switches and reconnects with no resync plumbing.
  const cdDetails = computed(() => {
    const state = unifiedStore.systemState;
    return state.source === 'cd' && state.details?.kind === 'cd' ? state.details : null;
  });

  // === DRIVE / DISC STATE ===
  // {id, album, artist, year, cover_url, tracks: [{number, title, duration_ms}]}
  const discInfo = computed(() => cdDetails.value?.disc ?? null);

  const tracks = computed(() => discInfo.value?.tracks ?? []);
  const currentTrack = computed(() => cdDetails.value?.current_track ?? null);

  // === PLAYBACK STATE ===
  const isPlaying = computed(() =>
    !!cdDetails.value && unifiedStore.systemState.session?.phase === 'playing'
  );

  // === UI STATE ===
  const showTracklist = ref(false);

  // === PLAYBACK ACTIONS ===
  async function playTrack(trackNumber) {
    await unifiedStore.sendCommand('cd', 'play_track', { track_number: trackNumber });
  }

  async function eject() {
    await unifiedStore.sendCommand('cd', 'eject');
  }

  // === UI ACTIONS ===
  function toggleTracklist() {
    showTracklist.value = !showTracklist.value;
  }

  return {
    // State (all derived from the central mirror)
    discInfo,
    tracks,
    currentTrack,
    isPlaying,
    showTracklist,

    // Actions
    playTrack,
    eject,
    toggleTracklist,
  };
});
