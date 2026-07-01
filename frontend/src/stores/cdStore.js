// frontend/src/stores/cdStore.js
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import { apiCall } from '@/services/apiCall';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';

export const useCdStore = defineStore('cd', () => {
  const unifiedStore = useUnifiedAudioStore();

  // === DISC & TRACK STATE ===
  const discInfo = ref(null); // { disc_id, album, artist, year, album_art_url, track_count }
  const tracks = ref([]); // [{ number, title, duration }]
  const currentTrack = ref(null); // 1-based track number

  // Derived from unified store (single source of truth for playback state)
  const isPlaying = computed(() =>
    unifiedStore.systemState.active_source === 'cd'
      && !!unifiedStore.systemState.metadata?.is_playing
  );
  // === DRIVE STATE ===
  const discPresent = ref(false);

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

  async function fetchDriveStatus() {
    const result = await apiCall.get('/api/cd/drive-status', {
      category: 'cd',
      message: 'Error fetching drive status',
    });
    if (result.ok) {
      discPresent.value = result.data.disc_present ?? false;
    }
  }

  // === UI ACTIONS ===
  function toggleTracklist() {
    showTracklist.value = !showTracklist.value;
  }

  // === WEBSOCKET EVENT HANDLERS ===

  // Applies an already-flat CD metadata object to the store state.
  function _applyMetadata(metadata) {
    if (metadata.disc_id !== undefined) {
      // Disc identity comes from the persistent extras (disc_*), which survive
      // WAITING and a WS reconnect — unlike the core now-playing projection.
      discInfo.value = {
        disc_id: metadata.disc_id,
        album: metadata.disc_album,
        artist: metadata.disc_artist,
        year: metadata.disc_year,
        album_art_url: metadata.disc_cover_url,
        track_count: metadata.track_count,
      };
      discPresent.value = !!metadata.disc_id;
    }

    if (metadata.tracks !== undefined) {
      tracks.value = metadata.tracks || [];
    }

    if (metadata.current_track !== undefined) {
      currentTrack.value = metadata.current_track;
    }
  }

  // Called from App.vue on system.initial_state / system.state_changed when
  // full_state.active_source === 'cd' and metadata is already flat.
  function handleInitialMetadata(metadata) {
    _applyMetadata(metadata);
  }

  // Called from App.vue on source.state_changed; metadata is nested under
  // event.data.metadata (the event also carries old_state/new_state).
  function handleSourceEvent(event) {
    if (event.origin !== 'cd') return;
    if (event.type === 'state_changed') {
      _applyMetadata(event.data?.metadata || {});
    }
  }

  function handleSystemEvent(event) {
    if (event.type === 'cd_drive_status') {
      discPresent.value = event.data?.disc_present ?? false;

      // Clear disc state when disc is removed (eject or physical removal)
      if (!discPresent.value) {
        discInfo.value = null;
        tracks.value = [];
        currentTrack.value = null;
      }
    }
  }

  async function resync() {
    return fetchDriveStatus();
  }

  return {
    resync,
    // State
    discInfo,
    tracks,
    currentTrack,
    isPlaying,
    showTracklist,

    // Actions
    playTrack,
    eject,
    fetchDriveStatus,
    toggleTracklist,

    // WS handlers
    handleInitialMetadata,
    handleSourceEvent,
    handleSystemEvent,
  };
});
