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
import { useSettingsStore } from '@/stores/settingsStore';
import { useI18n } from '@/services/i18n';
import { formatDeviceNames } from '@/utils/deviceName';
import { getFaviconUrl } from '@/utils/faviconUrl';
import { AIRPLAY_MIN_ARTWORK_PX } from '@/constants/imageQuality';

/** Minimum ms between activity event processing. */
const ACTIVITY_THROTTLE_MS = 500;

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
  const settingsStore = useSettingsStore();
  const { t } = useI18n();
  const timer = useTimer();

  const {
    currentPosition: podcastPosition,
    duration: podcastDuration,
    progressPercentage: podcastProgressPercentage,
    isPositionInitialized: podcastProgressReady,
  } = useSourceProgress('podcast');

  // --- Reactive state ---
  const isScreensaverVisible = ref(false);
  let inactivityTimer = null;
  let lastActivityTime = 0;

  // --- Derived settings ---

  const screensaverDelay = computed(() =>
    (settingsStore.screenScreensaver.screensaver_delay_seconds ?? 15) * 1000
  );

  const shouldMonitorInactivity = computed(() => {
    if (!settingsStore.screenScreensaver.screensaver_enabled) return false;
    const source = unifiedStore.systemState.active_source;
    const state = unifiedStore.systemState.source_state;
    return ['radio', 'podcast', 'bluetooth', 'mac', 'airplay'].includes(source) && state === 'active';
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
        title: station?.name || 'Unknown station',
        subtitle: metaParts.length > 0 ? metaParts.join(' \u2022 ') : 'Live',
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
        title: episode?.name || 'No episode',
        subtitle: episode?.podcast?.name || null,
        stationFavicon: null,
        stationName: null,
      };
    }

    if (source === 'airplay') {
      const metadata = unifiedStore.systemState.metadata || {};
      const deviceName = metadata.client_name || null;

      // Rich media layout only while audio is actually flowing AND the sender
      // pushes a real cover (>300px — same gate as the main now-playing view).
      // When playback stops but the route stays connected (e.g. quitting the
      // sender app: AirPlay emits `pend`, not `disc`), the backend keeps the
      // stale title/artwork but flips is_playing=false — so we fall back to the
      // simple "Connected to <device>" card rather than show a cover for audio
      // that no longer plays.
      const showRichMedia = !!metadata.is_playing && !!metadata.title &&
        !!metadata.artist && (metadata.album_art_width || 0) > AIRPLAY_MIN_ARTWORK_PX;

      if (showRichMedia) {
        return {
          mode: 'media',
          artwork: metadata.album_art_url || null,
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
      return {
        mode: 'simple',
        sourceType: 'bluetooth',
        title: t('status.connectedTo'),
        subtitle: formatDeviceNames(metadata.device_name),
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

    // Fallback (unreachable: shouldMonitorInactivity gates the screensaver)
    return {
      mode: 'media',
      artwork: null,
      title: '',
      subtitle: null,
      stationFavicon: null,
      stationName: null,
    };
  });

  const screensaverProgress = computed(() => {
    if (unifiedStore.systemState.active_source !== 'podcast') return null;
    return {
      currentPosition: podcastPosition.value,
      duration: podcastDuration.value,
      progressPercentage: podcastProgressPercentage.value,
      isReady: podcastProgressReady.value,
    };
  });

  // --- Watchers ---

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
    screensaverData,
    screensaverProgress,
    closeScreensaver,
  };
}
