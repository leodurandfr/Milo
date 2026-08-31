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
import { useRichDisplay } from '@/composables/useRichDisplay';
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

  // Which layout the screensaver draws is not its own decision: it mirrors
  // useRichDisplay, the rule that already picks between a rich player and the
  // AudioSourceStatus card for the view sitting behind this overlay. Deciding
  // it twice is exactly how DLNA came to draw a full media card — with a
  // generated text avatar standing in for the cover it did not have — over a
  // status card that had already refused it for want of one. AirPlay and
  // Bluetooth used to restate the rule here verbatim; they now read it from
  // the same place as every other source, and the copies are gone.
  const { richSource } = useRichDisplay();

  /**
   * The full-screen restatement of a rich player: cover, title, subtitle, and
   * for the receivers a bottom bar naming the other end.
   *
   * No branch resolves its own no-cover fallback — `artwork` may be empty and
   * AudioScreensaver asks the shared helper what fills the slot, so the player
   * behind it cannot be showing something else.
   */
  function mediaData(source) {
    if (source === 'radio') {
      const station = radioStore.currentStation;
      const track = radioStore.trackInfo;

      // Favicon URL only — AudioScreensaver renders the inline SVG avatar from
      // `stationName` so the font cascades correctly. Radio is the one source
      // the helper answers 'avatar' for.
      const stationArt = getFaviconUrl(station?.favicon);

      if (track) {
        return {
          mode: 'media',
          sourceType: source,
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
        sourceType: source,
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
        sourceType: source,
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
        sourceType: source,
        artwork: track?.albumArtUrl || null,
        title: track?.title || '',
        subtitle: track?.artist || null,
        stationFavicon: null,
        stationName: null,
      };
    }

    const metadata = unifiedStore.systemState.metadata || {};

    // Spotify, Tidal and CD: active players with rich metadata, rendered exactly
    // like music_library (cover + title/artist + progress bar, no bottom bar),
    // read straight from the shared metadata mirror. Every cover below comes
    // from nowPlayingArtwork, which is also what AudioPlayerFull paints — the
    // screensaver crossfades into that view, so anything else reads as a glitch.
    if (source === 'spotify' || source === 'tidal' || source === 'cd') {
      return {
        mode: 'media',
        sourceType: source,
        artwork: nowPlayingArtwork(metadata),
        title: metadata.title || '',
        subtitle: metadata.artist || null,
      };
    }

    // The four receivers. The bottom bar mirrors their main view's source bar
    // down to the fallback: DLNA names the media server once resolved and Qobuz
    // names nobody at all, so with no name on the record both read the source's
    // own label rather than leaving the slot empty. Bluetooth and AirPlay name
    // the sender. No progress bar — none of them shows one in its main view.
    if (source === 'bluetooth') {
      return {
        mode: 'media',
        sourceType: source,
        artwork: nowPlayingArtwork(metadata),
        title: metadata.title || '',
        subtitle: metadata.artist || null,
        stationIcon: source,
        stationName: formatDeviceNames(metadata.device_name),
      };
    }

    if (source === 'airplay' || source === 'qobuz' || source === 'dlna') {
      return {
        mode: 'media',
        sourceType: source,
        artwork: nowPlayingArtwork(metadata),
        title: metadata.title || '',
        subtitle: metadata.artist || null,
        stationIcon: source,
        stationName: source === 'airplay'
          ? metadata.client_name || null
          : metadata.client_name || t(AUDIO_SOURCE_LABEL_KEYS[source]),
      };
    }

    // Unreachable: a source with no branch here has no rich view either, so
    // richSource sent it to simpleData. Carries no `artwork` key on purpose —
    // the prop defaults to null, and a literal here would be the one thing the
    // parity guard cannot tell apart from a source that forgot to derive its
    // cover.
    return {
      mode: 'media',
      sourceType: null,
      title: '',
      subtitle: null,
      stationFavicon: null,
      stationName: null,
    };
  }

  /**
   * The full-screen restatement of the AudioSourceStatus card: the source
   * glyph, a status line, and whoever is on the other end.
   *
   * Only the receivers genuinely reach it — every other source earns a rich
   * view whenever it is active — but the default keeps a transition or an
   * unavailable source from rendering a blank overlay.
   */
  function simpleData(source) {
    const metadata = unifiedStore.systemState.metadata || {};

    if (source === 'mac') {
      return {
        mode: 'simple',
        sourceType: source,
        title: t('status.audioReceivedFrom'),
        subtitle: formatDeviceNames(metadata.client_names),
      };
    }

    if (source === 'bluetooth') {
      return {
        mode: 'simple',
        sourceType: source,
        title: t('status.connectedTo'),
        subtitle: formatDeviceNames(metadata.device_name),
      };
    }

    if (source === 'airplay' || source === 'dlna' || source === 'qobuz') {
      return {
        mode: 'simple',
        sourceType: source,
        title: t('status.connectedTo'),
        subtitle: metadata.client_name || null,
      };
    }

    const labelKey = AUDIO_SOURCE_LABEL_KEYS[source];
    return {
      mode: 'simple',
      sourceType: labelKey ? source : null,
      title: labelKey ? t(labelKey) : '',
      subtitle: null,
    };
  }

  const screensaverData = computed(() => {
    const source = unifiedStore.systemState.active_source;
    return richSource.value === null ? simpleData(source) : mediaData(source);
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
