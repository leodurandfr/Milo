// frontend/src/composables/useScreensaver.js
// Screensaver visibility, inactivity timer, activity listeners, and display-data
// computation for AudioScreensaver. Owns the full screensaver lifecycle so MainView
// only needs to render the component and wire the returned refs.
import { ref, computed, watch, onUnmounted } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useRadioStore } from '@/stores/radioStore';
import { usePodcastStore } from '@/stores/podcastStore';
import { useSettingsStore } from '@/stores/settingsStore';
import { useI18n } from '@/services/i18n';
import { formatDeviceNames } from '@/utils/deviceName';

/** Minimum ms between activity event processing. */
const ACTIVITY_THROTTLE_MS = 500;

/**
 * Resolve a radio station favicon through the backend proxy to avoid CORS.
 * Custom-uploaded images already have an internal path and are returned as-is.
 *
 * @param {Object|null} station
 * @returns {string|null}
 */
function stationArtworkUrl(station) {
  const favicon = station?.favicon;
  if (!favicon) return null;
  if (favicon.startsWith('/api/radio/images/')) return favicon;
  return `/api/radio/favicon?url=${encodeURIComponent(favicon)}`;
}

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
    const state = unifiedStore.systemState.plugin_state;
    return ['radio', 'podcast', 'bluetooth', 'mac'].includes(source) && state === 'connected';
  });

  // --- Timer management ---

  function clearInactivityTimer() {
    if (inactivityTimer) {
      clearTimeout(inactivityTimer);
      inactivityTimer = null;
    }
  }

  function resetInactivityTimer() {
    clearInactivityTimer();
    if (!shouldMonitorInactivity.value || isScreensaverVisible.value) return;

    inactivityTimer = setTimeout(() => {
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

      if (track) {
        return {
          mode: 'media',
          artwork: track.artwork || stationArtworkUrl(station),
          title: track.title,
          subtitle: track.artist || null,
          stationFavicon: stationArtworkUrl(station),
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
        artwork: stationArtworkUrl(station),
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

    if (source === 'bluetooth') {
      const metadata = unifiedStore.systemState.metadata || {};
      return {
        mode: 'simple',
        pluginType: 'bluetooth',
        title: t('status.connectedTo'),
        subtitle: formatDeviceNames(metadata.device_name),
      };
    }

    if (source === 'mac') {
      const metadata = unifiedStore.systemState.metadata || {};
      return {
        mode: 'simple',
        pluginType: 'mac',
        title: t('status.audioReceivedFrom'),
        subtitle: formatDeviceNames(metadata.client_names || metadata.client_name),
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
    clearInactivityTimer();
  });

  return {
    isScreensaverVisible,
    screensaverData,
    closeScreensaver,
  };
}
