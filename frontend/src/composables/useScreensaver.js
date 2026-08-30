// frontend/src/composables/useScreensaver.js
// Screensaver visibility, inactivity timer, activity listeners, and display-data
// computation for AudioScreensaver. Owns the full screensaver lifecycle so MainView
// only needs to render the component and wire the returned refs.
import { ref, computed, watch, onUnmounted } from 'vue';
import { useTimer } from '@/composables/useTimer';
import { useSourceProgress } from '@/composables/useSourceProgress';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useRadioStore } from '@/stores/radioStore';
import { usePodcastStore } from '@/stores/podcastStore';
import { useMusicLibraryStore } from '@/stores/musicLibraryStore';
import { useSettingsStore } from '@/stores/settingsStore';
import { useLyricsStore } from '@/stores/lyricsStore';
import { useI18n } from '@/services/i18n';
import { isKiosk } from '@/utils/kiosk';
import { formatDeviceNames } from '@/utils/deviceName';
import { getFaviconUrl } from '@/utils/faviconUrl';
import { nowPlayingArtwork } from '@/utils/nowPlayingArtwork';
import { UNTRUSTED_SENDER_MIN_ARTWORK_PX } from '@/constants/imageQuality';
import { AUDIO_SOURCE_LABEL_KEYS } from '@/constants/audioSources';

/** Minimum ms between activity event processing. */
const ACTIVITY_THROTTLE_MS = 500;

// Media sources: the screensaver shows only while audio is actually playing, so
// pausing closes it (the backend otherwise keeps the last track's metadata
// stale). The two passive receivers below have no play/pause concept — their
// screensaver stays up while the sender is connected (source_state 'active').
const PLAYBACK_GATED_SOURCES = ['radio', 'podcast', 'airplay', 'dlna', 'qobuz', 'music_library', 'spotify', 'cd', 'tidal'];
const PASSIVE_SOURCES = ['bluetooth', 'mac'];

/**
 * Manages the audio screensaver lifecycle: visibility, inactivity timer,
 * DOM activity listeners, and display data derived from the active source.
 *
 * @returns {{
 *   isScreensaverVisible: import('vue').Ref<boolean>,
 *   screensaverData: import('vue').ComputedRef<Object>,
 *   closeScreensaver: () => void
 * }}
 */
export function useScreensaver() {
  const unifiedStore = useUnifiedAudioStore();
  const radioStore = useRadioStore();
  const podcastStore = usePodcastStore();
  const musicLibraryStore = useMusicLibraryStore();
  const settingsStore = useSettingsStore();
  const lyricsStore = useLyricsStore();
  const { t } = useI18n();
  const timer = useTimer();

  const {
    currentPosition: podcastPosition,
    duration: podcastDuration,
    progressPercentage: podcastProgressPercentage,
    isPositionInitialized: podcastProgressReady,
  } = useSourceProgress('podcast');

  const {
    currentPosition: libraryPosition,
    duration: libraryDuration,
    progressPercentage: libraryProgressPercentage,
    isPositionInitialized: libraryProgressReady,
  } = useSourceProgress('music_library');

  const {
    currentPosition: spotifyPosition,
    duration: spotifyDuration,
    progressPercentage: spotifyProgressPercentage,
    isPositionInitialized: spotifyProgressReady,
  } = useSourceProgress('spotify');

  const {
    currentPosition: cdPosition,
    duration: cdDuration,
    progressPercentage: cdProgressPercentage,
    isPositionInitialized: cdProgressReady,
  } = useSourceProgress('cd');

  // --- Reactive state ---
  const isScreensaverVisible = ref(false);
  // Bumped on each close (visible → hidden) so a revealed source view can replay
  // its entrance animation — consumed via useScreensaverReveal.
  const screensaverRevealNonce = ref(0);
  let inactivityTimer = null;
  let lastActivityTime = 0;

  // --- Derived settings ---

  const screensaverDelay = computed(() =>
    (settingsStore.screenScreensaver.screensaver_delay_seconds ?? 15) * 1000
  );

  const shouldMonitorInactivity = computed(() => {
    // Pi-screen-only: the screensaver is the physical display's idle state, so a
    // remote Mac/iPhone viewing the UI never shows it (matches ui_scale + color
    // filter). Also removes the need for the portrait CSS hide hack it once used.
    if (!isKiosk()) return false;
    if (!settingsStore.screenScreensaver.screensaver_enabled) return false;
    // Lyrics is itself a full-screen reading view that scrolls on its own: covering
    // it after a delay would hide the thing being read, without any user inactivity.
    if (lyricsStore.isOpen) return false;
    if (unifiedStore.systemState.source_state !== 'active') return false;
    const source = unifiedStore.systemState.active_source;
    if (PASSIVE_SOURCES.includes(source)) return true;
    if (PLAYBACK_GATED_SOURCES.includes(source)) {
      return unifiedStore.systemState.metadata?.is_playing === true;
    }
    return false;
  });

  // --- Timer management ---

  function clearInactivityTimer() {
    if (inactivityTimer) {
      timer.clear(inactivityTimer);
      inactivityTimer = null;
    }
  }

  function resetInactivityTimer() {
    clearInactivityTimer();
    if (!shouldMonitorInactivity.value || isScreensaverVisible.value) return;

    inactivityTimer = timer.setTimeout(() => {
      isScreensaverVisible.value = true;
    }, screensaverDelay.value);
  }

  // --- Activity handling ---

  function handleUserActivity() {
    const now = Date.now();
    if (now - lastActivityTime < ACTIVITY_THROTTLE_MS) return;
    lastActivityTime = now;

    if (!isScreensaverVisible.value) {
      resetInactivityTimer();
    }
  }

  // --- DOM listener management ---

  function addActivityListeners() {
    document.addEventListener('pointerdown', handleUserActivity, { passive: true });
    document.addEventListener('wheel', handleUserActivity, { passive: true });
    document.addEventListener('touchstart', handleUserActivity, { passive: true });
  }

  function removeActivityListeners() {
    document.removeEventListener('pointerdown', handleUserActivity);
    document.removeEventListener('wheel', handleUserActivity);
    document.removeEventListener('touchstart', handleUserActivity);
  }

  // --- Public action ---

  function closeScreensaver() {
    isScreensaverVisible.value = false;
    resetInactivityTimer();
  }

  // --- Screensaver display data ---

  const screensaverData = computed(() => {
    const source = unifiedStore.systemState.active_source;

    if (source === 'radio') {
      const station = radioStore.currentStation;
      const track = radioStore.trackInfo;

      // Favicon URL only — AudioScreensaver renders the inline SVG fallback
      // from `stationName` so the avatar font cascades correctly.
      const stationArt = getFaviconUrl(station?.favicon);

      if (track) {
        return {
          mode: 'media',
          artwork: track.artwork || stationArt,
          title: track.title,
          subtitle: track.artist || null,
          stationFavicon: stationArt,
          stationName: station?.name || null,
        };
      }

      const genre = station?.genre
        ? station.genre.charAt(0).toUpperCase() + station.genre.slice(1)
        : null;
      const bitrate = station?.bitrate > 0 ? `${station.bitrate} kbps` : null;
      const metaParts = [genre, bitrate].filter(Boolean);

      return {
        mode: 'media',
        artwork: stationArt,
        title: station?.name || t('radio.unknownStation'),
        subtitle: metaParts.length > 0 ? metaParts.join(' \u2022 ') : t('radio.live'),
        stationFavicon: null,
        stationName: null,
        useMonoSubtitle: true,
      };
    }

    if (source === 'podcast') {
      const episode = podcastStore.displayEpisode;
      return {
        mode: 'media',
        artwork: episode?.image_url || null,
        title: episode?.name || t('podcasts.noEpisode'),
        subtitle: episode?.podcast?.name || null,
        stationFavicon: null,
        stationName: null,
      };
    }

    if (source === 'music_library') {
      const track = musicLibraryStore.displayTrack;
      return {
        mode: 'media',
        artwork: track?.albumArtUrl || null,
        title: track?.title || '',
        subtitle: track?.artist || null,
        stationFavicon: null,
        stationName: null,
      };
    }

    // Spotify + Tidal + CD: active players with rich metadata, rendered exactly
    // like music_library (cover + title/artist + progress bar, no bottom bar),
    // read straight from the shared metadata mirror. Every cover below comes
    // from nowPlayingArtwork, which is also what AudioPlayerFull paints — the
    // screensaver crossfades into that view, so anything else reads as a glitch.
    if (source === 'spotify' || source === 'tidal') {
      const metadata = unifiedStore.systemState.metadata || {};
      return {
        mode: 'media',
        artwork: nowPlayingArtwork(source, metadata),
        title: metadata.title || '',
        subtitle: metadata.artist || null,
      };
    }

    if (source === 'cd') {
      const metadata = unifiedStore.systemState.metadata || {};
      return {
        mode: 'media',
        artwork: nowPlayingArtwork(source, metadata),
        title: metadata.title || '',
        subtitle: metadata.artist || null,
      };
    }

    // Qobuz + DLNA: passive players (external control, rich metadata) like
    // AirPlay. Visibility is gated on is_playing, so a bare media layout suffices
    // here — no "connected but idle" fallback. The bottom bar mirrors their main
    // view's source bar down to the fallback: DLNA names the media server once
    // resolved and Qobuz names nobody at all, so with no name on the record both
    // read the source's own label rather than leaving the slot empty. No progress
    // bar — neither shows one in its main view (showControls=false).
    if (source === 'qobuz' || source === 'dlna') {
      const metadata = unifiedStore.systemState.metadata || {};
      return {
        mode: 'media',
        artwork: nowPlayingArtwork(source, metadata),
        title: metadata.title || '',
        subtitle: metadata.artist || null,
        stationIcon: source,
        stationName: metadata.client_name || t(AUDIO_SOURCE_LABEL_KEYS[source]),
      };
    }

    if (source === 'airplay') {
      const metadata = unifiedStore.systemState.metadata || {};
      const deviceName = metadata.client_name || null;

      // Visibility already guarantees is_playing (a pause closes the screensaver).
      // Show the rich media card only when the sender also pushes real metadata +
      // a real cover (>300px — same gate as the main now-playing view); a stream
      // that plays with no usable metadata (e.g. a game or web video) falls back
      // to the simple "Connected to <device>" card instead of an empty cover.
      const showRichMedia = !!metadata.title && !!metadata.artist &&
        (metadata.album_art_width || 0) > UNTRUSTED_SENDER_MIN_ARTWORK_PX;

      if (showRichMedia) {
        return {
          mode: 'media',
          artwork: nowPlayingArtwork(source, metadata),
          title: metadata.title,
          subtitle: metadata.artist || null,
          // Bottom bar shows the AirPlay glyph + sender name, in the same slot
          // the radio layout uses for station favicon + station name.
          stationIcon: 'airplay',
          stationName: deviceName,
        };
      }

      return {
        mode: 'simple',
        sourceType: 'airplay',
        title: t('status.connectedTo'),
        subtitle: deviceName,
      };
    }

    if (source === 'bluetooth') {
      const metadata = unifiedStore.systemState.metadata || {};
      const deviceName = formatDeviceNames(metadata.device_name);

      // Same hybrid as AirPlay, on the gate Bluetooth can actually satisfy:
      // title + artist, since AVRCP carries no cover of its own. The artwork is
      // whatever the backend resolved from the track text — through the shared
      // helper like every other branch, so it is by construction the cover
      // AudioPlayerFull is showing behind this screensaver. It stays a
      // PASSIVE_SOURCE for *visibility* though: a sender exposing no AVRCP
      // player never sets is_playing, and gating on it would mean a connected
      // phone that simply never gets a screensaver.
      if (metadata.title && metadata.artist) {
        return {
          mode: 'media',
          artwork: nowPlayingArtwork(source, metadata),
          title: metadata.title,
          subtitle: metadata.artist,
          stationIcon: 'bluetooth',
          stationName: deviceName,
        };
      }

      return {
        mode: 'simple',
        sourceType: 'bluetooth',
        title: t('status.connectedTo'),
        subtitle: deviceName,
      };
    }

    if (source === 'mac') {
      const metadata = unifiedStore.systemState.metadata || {};
      return {
        mode: 'simple',
        sourceType: 'mac',
        title: t('status.audioReceivedFrom'),
        subtitle: formatDeviceNames(metadata.client_names),
      };
    }

    // Fallback (unreachable: shouldMonitorInactivity gates the screensaver).
    // Carries no `artwork` key on purpose — the prop defaults to null, and a
    // literal here would be the one thing the parity guard cannot tell apart
    // from a source that forgot to derive its cover.
    return {
      mode: 'media',
      title: '',
      subtitle: null,
      stationFavicon: null,
      stationName: null,
    };
  });

  const screensaverProgress = computed(() => {
    const source = unifiedStore.systemState.active_source;

    if (source === 'podcast') {
      return {
        currentPosition: podcastPosition.value,
        duration: podcastDuration.value,
        progressPercentage: podcastProgressPercentage.value,
        isReady: podcastProgressReady.value,
      };
    }

    if (source === 'music_library') {
      return {
        currentPosition: libraryPosition.value,
        duration: libraryDuration.value,
        progressPercentage: libraryProgressPercentage.value,
        isReady: libraryProgressReady.value,
      };
    }

    if (source === 'spotify') {
      return {
        currentPosition: spotifyPosition.value,
        duration: spotifyDuration.value,
        progressPercentage: spotifyProgressPercentage.value,
        isReady: spotifyProgressReady.value,
      };
    }

    if (source === 'cd') {
      return {
        currentPosition: cdPosition.value,
        duration: cdDuration.value,
        progressPercentage: cdProgressPercentage.value,
        isReady: cdProgressReady.value,
      };
    }

    return null;
  });

  // --- Watchers ---

  watch(isScreensaverVisible, (visible, wasVisible) => {
    if (wasVisible && !visible) screensaverRevealNonce.value += 1;
  });

  watch(shouldMonitorInactivity, (shouldMonitor) => {
    if (shouldMonitor) {
      addActivityListeners();
      resetInactivityTimer();
    } else {
      removeActivityListeners();
      clearInactivityTimer();
      isScreensaverVisible.value = false;
    }
  }, { immediate: true });

  watch(
    () => settingsStore.screenScreensaver.screensaver_delay_seconds,
    () => {
      if (shouldMonitorInactivity.value && !isScreensaverVisible.value) {
        resetInactivityTimer();
      }
    }
  );

  // --- Cleanup ---

  onUnmounted(() => {
    removeActivityListeners();
    // inactivityTimer is auto-cleared by useTimer.
  });

  return {
    isScreensaverVisible,
    screensaverRevealNonce,
    screensaverData,
    screensaverProgress,
    closeScreensaver,
  };
}
