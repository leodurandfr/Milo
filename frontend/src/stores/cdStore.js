// frontend/src/stores/cdStore.js
import { defineStore } from 'pinia';
import { ref, computed, watch } from 'vue';
import axios from 'axios';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { apiCall } from '@/services/apiCall';

export const useCdStore = defineStore('cd', () => {
  // === DISC & TRACK STATE ===
  const discInfo = ref(null); // { disc_id, album, artist, year, cover_url, track_count, total_duration }
  const tracks = ref([]); // [{ number, title, duration }]
  const currentTrack = ref(null); // 1-based track number
  const trackPosition = ref(0); // seconds
  const trackDuration = ref(0); // seconds
  const isPlaying = ref(false);
  const isBuffering = ref(false);
  const albumFinished = ref(false);

  // === DRIVE STATE ===
  const driveConnected = ref(false);
  const discPresent = ref(false);

  // === UI STATE ===
  const showTracklist = ref(false);

  // === PROGRESS INTERPOLATION ===
  let progressTimer = null;

  function startProgressTimer() {
    stopProgressTimer();
    progressTimer = setInterval(() => {
      if (isPlaying.value && trackPosition.value < trackDuration.value) {
        trackPosition.value += 0.1;
      }
    }, 100);
  }

  function stopProgressTimer() {
    if (progressTimer) {
      clearInterval(progressTimer);
      progressTimer = null;
    }
  }

  // Watch isPlaying to start/stop interpolation
  watch(isPlaying, (playing) => {
    if (playing) {
      startProgressTimer();
    } else {
      stopProgressTimer();
    }
  });

  // === COMPUTED ===
  const positionMs = computed(() => trackPosition.value * 1000);
  const durationMs = computed(() => trackDuration.value * 1000);
  const progressPercentage = computed(() => {
    if (!trackDuration.value) return 0;
    return (trackPosition.value / trackDuration.value) * 100;
  });

  const currentTrackInfo = computed(() => {
    if (!currentTrack.value || !tracks.value.length) return null;
    return tracks.value.find(t => t.number === currentTrack.value) || null;
  });

  // === PLAYBACK ACTIONS ===
  async function playTrack(trackNumber) {
    console.log(`[CD TIMING] playTrack(${trackNumber}) called at ${Date.now()}`);
    await apiCall('cd', 'Error playing track', () =>
      axios.post('/api/cd/play', { track_number: trackNumber })
    );
    console.log(`[CD TIMING] playTrack(${trackNumber}) API returned at ${Date.now()}`);
  }

  async function stop() {
    await apiCall('cd', 'Error stopping', () =>
      axios.post('/api/cd/stop')
    );
  }

  async function pause() {
    await apiCall('cd', 'Error pausing', () =>
      axios.post('/api/cd/pause')
    );
  }

  async function resume() {
    await apiCall('cd', 'Error resuming', () =>
      axios.post('/api/cd/resume')
    );
  }

  async function togglePlayPause() {
    console.log(`[CD TIMING] togglePlayPause: isPlaying=${isPlaying.value}, albumFinished=${albumFinished.value}`);
    if (albumFinished.value) {
      await playTrack(1);
    } else if (isPlaying.value) {
      await pause();
    } else {
      await resume();
    }
  }

  async function nextTrack() {
    console.log(`[CD TIMING] nextTrack() called, currentTrack=${currentTrack.value}`);
    await apiCall('cd', 'Error next track', () =>
      axios.post('/api/cd/next')
    );
    console.log(`[CD TIMING] nextTrack() API returned`);
  }

  async function prevTrack() {
    await apiCall('cd', 'Error prev track', () =>
      axios.post('/api/cd/prev')
    );
  }

  async function seek(position) {
    const prev = trackPosition.value;
    trackPosition.value = position;
    const ok = await apiCall('cd', 'Error seeking', () =>
      axios.post('/api/cd/seek', { position: Math.floor(position) })
    );
    if (!ok) trackPosition.value = prev;
  }

  async function eject() {
    await apiCall('cd', 'Error ejecting', () =>
      axios.post('/api/cd/eject')
    );
  }

  async function fetchDriveStatus() {
    await apiCall('cd', 'Error fetching drive status', async () => {
      const response = await axios.get('/api/cd/drive-status');
      driveConnected.value = response.data.connected ?? false;
      discPresent.value = response.data.disc_present ?? false;
    });
  }

  // === UI ACTIONS ===
  function toggleTracklist() {
    showTracklist.value = !showTracklist.value;
  }

  // === WEBSOCKET EVENT HANDLERS ===
  function handlePluginEvent(event) {
    if (event.source !== 'cd') return;

    if (event.type === 'state_changed') {
      const metadata = event.data?.metadata || event.data || {};

      // Log state changes with timing
      const changes = [];
      if (metadata.is_playing !== undefined && metadata.is_playing !== isPlaying.value)
        changes.push(`is_playing: ${isPlaying.value}→${metadata.is_playing}`);
      if (metadata.current_track !== undefined && metadata.current_track !== currentTrack.value)
        changes.push(`track: ${currentTrack.value}→${metadata.current_track}`);
      if (metadata.track_position !== undefined && Math.abs(metadata.track_position - trackPosition.value) > 2)
        changes.push(`pos: ${trackPosition.value.toFixed(1)}→${metadata.track_position}`);
      if (metadata.track_duration !== undefined && metadata.track_duration !== trackDuration.value)
        changes.push(`dur: ${trackDuration.value}→${metadata.track_duration}`);
      if (changes.length > 0)
        console.log(`[CD TIMING] WS state_changed: ${changes.join(', ')}`);

      // Disc info
      if (metadata.disc_id !== undefined) {
        discInfo.value = {
          disc_id: metadata.disc_id,
          album: metadata.album,
          artist: metadata.artist,
          year: metadata.year,
          cover_url: metadata.cover_url,
          track_count: metadata.track_count,
          total_duration: metadata.total_duration
        };
      }

      // Track list
      if (metadata.tracks !== undefined) {
        tracks.value = metadata.tracks || [];
      }

      // Playback state
      if (metadata.current_track !== undefined) {
        currentTrack.value = metadata.current_track;
      }
      if (metadata.track_position !== undefined) {
        trackPosition.value = metadata.track_position;
      }
      if (metadata.track_duration !== undefined) {
        trackDuration.value = metadata.track_duration;
      }
      if (metadata.is_playing !== undefined) {
        isPlaying.value = metadata.is_playing;
      }
      if (metadata.is_buffering !== undefined) {
        isBuffering.value = metadata.is_buffering;
      }
      if (metadata.album_finished !== undefined) {
        albumFinished.value = metadata.album_finished;
      }

      // Disc presence from plugin state
      if (metadata.disc_id !== undefined) {
        discPresent.value = !!metadata.disc_id;
      }
    }
  }

  function handleSystemEvent(event) {
    if (event.type === 'cd_drive_status') {
      driveConnected.value = event.data?.drive_connected ?? false;
      discPresent.value = event.data?.disc_present ?? false;
    }
  }

  return {
    // State
    discInfo,
    tracks,
    currentTrack,
    trackPosition,
    trackDuration,
    isPlaying,
    isBuffering,
    albumFinished,
    driveConnected,
    discPresent,
    showTracklist,

    // Computed
    positionMs,
    durationMs,
    progressPercentage,
    currentTrackInfo,

    // Playback actions
    playTrack,
    stop,
    pause,
    resume,
    togglePlayPause,
    nextTrack,
    prevTrack,
    seek,
    eject,
    fetchDriveStatus,

    // UI actions
    toggleTracklist,

    // WS handlers
    handlePluginEvent,
    handleSystemEvent
  };
});
