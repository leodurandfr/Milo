// frontend/src/stores/cdStore.js
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';

export const useCdStore = defineStore('cd', () => {
  const unifiedStore = useUnifiedAudioStore();

  // All disc + playback state is derived from the central audio mirror
  // (unifiedAudioStore.systemState.metadata), the single source of truth. The
  // CD source publishes disc identity as persistent extras (disc_*) that survive
  // WAITING and a WS reconnect, so deriving here — rather than maintaining
  // delta-fed refs — keeps the store in sync across source transitions with no
  // resync plumbing (a webradio→CD switch carries the disc extras in the
  // transition_complete full_state).
  const cdMeta = computed(() =>
    unifiedStore.systemState.active_source === 'cd'
      ? (unifiedStore.systemState.metadata || {})
      : {}
  );

  // === DRIVE / DISC STATE ===
  const discInfo = computed(() => {
    const m = cdMeta.value;
    if (!m.disc_id) return null;
    return {
      disc_id: m.disc_id,
      album: m.disc_album,
      artist: m.disc_artist,
      year: m.disc_year,
      album_art_url: m.disc_cover_url,
      track_count: m.track_count,
    };
  });

  const tracks = computed(() => cdMeta.value.tracks || []);
  const currentTrack = computed(() => cdMeta.value.current_track ?? null);

  // === PLAYBACK STATE ===
  const isPlaying = computed(() => !!cdMeta.value.is_playing);

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
