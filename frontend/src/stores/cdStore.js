// frontend/src/stores/cdStore.js
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import { apiCall } from '@/services/apiCall';
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
  const discPresent = computed(() => !!cdMeta.value.disc_present);

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
  // Drive spinning up before audio flows — distinct from idle, so the UI shows a
  // spinner instead of the idle play affordance.
  const isBuffering = computed(() => !!cdMeta.value.is_buffering);

  // === UI STATE ===
  const showTracklist = ref(false);

  // === PLAYBACK ACTIONS ===
  async function playTrack(trackNumber) {
    await apiCall.post('/api/cd/play', { track_number: trackNumber }, {
      category: 'cd',
      message: 'Error playing track',
    });
  }

  async function eject() {
    await apiCall.post('/api/cd/eject', null, {
      category: 'cd',
      message: 'Error ejecting',
    });
  }

  // === UI ACTIONS ===
  function toggleTracklist() {
    showTracklist.value = !showTracklist.value;
  }

  return {
    // State (all derived from the central mirror)
    discInfo,
    discPresent,
    tracks,
    currentTrack,
    isPlaying,
    isBuffering,
    showTracklist,

    // Actions
    playTrack,
    eject,
    toggleTracklist,
  };
});
