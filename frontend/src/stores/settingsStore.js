// frontend/src/stores/settingsStore.js
import { defineStore } from 'pinia';
import { ref } from 'vue';
import axios from 'axios';
import { logger } from '@/services/logger';
import { apiCall } from '@/services/apiCall';

export const useSettingsStore = defineStore('settings', () => {
  // === LOADING STATE ===
  const isLoading = ref(false);
  const hasLoaded = ref(false);

  // === LANGUAGE ===
  const language = ref('english');

  // === VOLUME (all values in dB) ===
  const volumeLimits = ref({
    min_db: -80.0,
    max_db: -21.0
  });

  const volumeStartup = ref({
    startup_volume_db: -60.0,
    restore_last_volume: false
  });

  // Note: step_mobile_db is in unifiedAudioStore.volumeState (single source of truth)
  // Only step_rotary_db is kept here as it's hardware-specific
  const volumeSteps = ref({
    step_rotary_db: 2.0
  });

  // === DOCK APPS ===
  const ALL_AUDIO_SOURCES = ['spotify', 'bluetooth', 'radio', 'podcast', 'airplay', 'mac'];

  const dockApps = ref({
    spotify: true,
    bluetooth: true,
    radio: true,
    podcast: true,
    airplay: true,
    mac: true,
    equalizer: true,
    multiroom: true,
    settings: true
  });

  // Ordered list of all audio sources (both enabled and disabled)
  const sourceOrder = ref([...ALL_AUDIO_SOURCES]);

  // === SPOTIFY ===
  const spotifyDisconnect = ref({
    auto_disconnect_delay: 10.0
  });

  // === PODCAST ===
  const podcastCredentials = ref({
    taddy_user_id: '',
    taddy_api_key: ''
  });

  // Podcast credentials status (checked at startup)
  const podcastCredentialsStatus = ref('unknown'); // 'unknown', 'valid', 'missing', 'invalid', 'rate_limited', 'error'
  const podcastApiUsage = ref(null); // requests_used (null if no valid credentials)
  const podcastCredentialsValidatedAt = ref(null); // Unix timestamp when credentials were validated

  // === AUDIO INACTIVITY ===
  const inactivityTimeout = ref({
    inactivity_timeout: 7200
  });

  // === RADIO ===
  const radioSettings = ref({
    shazam_enabled: true
  });

  // === SCREEN ===
  const isScreenSleeping = ref(false);

  const screenTimeout = ref({
    screen_timeout_enabled: true,
    screen_timeout_seconds: 10
  });

  const screenBrightness = ref({
    brightness_on: 5
  });

  const screenScreensaver = ref({
    screensaver_enabled: true,
    screensaver_delay_seconds: 30
  });

  // === ACTIONS ===

  /**
   * Factory: creates a partial-merge updater for an object ref.
   * Usage: const updateFoo = makeUpdater(fooRef)
   */
  function makeUpdater(target) {
    return (config) => { target.value = { ...target.value, ...config }; };
  }

  /**
   * Load all settings in parallel
   */
  async function loadAllSettings() {
    if (isLoading.value) return;

    isLoading.value = true;
    await apiCall('settings', 'Error loading settings:', async () => {
      // Single bulk request + podcast status (requires external API call)
      const [bulkResponse, podcastStatusResponse] = await Promise.all([
        axios.get('/api/settings/bulk').catch(() => ({ data: null })),
        axios.get('/api/settings/podcast-credentials/status').catch(() => ({ data: { status: 'error' } }))
      ]);

      const d = bulkResponse.data;
      if (d) {
        language.value = d.language ?? 'english';

        volumeLimits.value = {
          min_db: d.volume_limits?.min_db ?? -80.0,
          max_db: d.volume_limits?.max_db ?? -21.0
        };

        volumeStartup.value = {
          startup_volume_db: d.volume_startup?.startup_volume_db ?? -30.0,
          restore_last_volume: d.volume_startup?.restore_last_volume ?? false
        };

        volumeSteps.value.step_rotary_db = d.rotary_steps?.step_rotary_db ?? 2.0;

        if (d.dock_apps?.enabled_apps) {
          const enabledApps = d.dock_apps.enabled_apps;
          dockApps.value = {
            spotify: enabledApps.includes('spotify'),
            bluetooth: enabledApps.includes('bluetooth'),
            radio: enabledApps.includes('radio'),
            podcast: enabledApps.includes('podcast'),
            airplay: enabledApps.includes('airplay'),
            mac: enabledApps.includes('mac'),
            equalizer: enabledApps.includes('equalizer'),
            multiroom: enabledApps.includes('multiroom'),
            settings: enabledApps.includes('settings')
          };
          syncSourceOrder(enabledApps);
        }

        spotifyDisconnect.value = {
          auto_disconnect_delay: d.spotify_disconnect?.auto_disconnect_delay ?? 10.0
        };

        podcastCredentials.value = {
          taddy_user_id: d.podcast_credentials?.taddy_user_id ?? '',
          taddy_api_key: d.podcast_credentials?.taddy_api_key ?? ''
        };

        inactivityTimeout.value = {
          inactivity_timeout: d.inactivity_timeout?.inactivity_timeout ?? 7200
        };

        screenTimeout.value = {
          screen_timeout_enabled: d.screen_timeout?.screen_timeout_enabled ?? true,
          screen_timeout_seconds: d.screen_timeout?.screen_timeout_seconds ?? 10
        };

        screenBrightness.value = {
          brightness_on: d.screen_brightness?.brightness_on ?? 5
        };

        screenScreensaver.value = {
          screensaver_enabled: d.screen_screensaver?.screensaver_enabled ?? true,
          screensaver_delay_seconds: d.screen_screensaver?.screensaver_delay_seconds ?? 30
        };

        radioSettings.value = {
          shazam_enabled: d.radio_settings?.shazam_enabled ?? true
        };
      }

      // Podcast credentials status (separate — requires external API call)
      if (podcastStatusResponse.data) {
        podcastCredentialsStatus.value = podcastStatusResponse.data.status ?? 'error';
        podcastApiUsage.value = podcastStatusResponse.data.requests_used ?? null;
        podcastCredentialsValidatedAt.value = podcastStatusResponse.data.credentials_validated_at ?? null;
      }

      hasLoaded.value = true;
      logger.info('settings', 'All settings loaded successfully');
    });
    isLoading.value = false;
  }

  /**
   * Update language
   */
  function updateLanguage(newLanguage) {
    language.value = newLanguage;
  }

  const updateVolumeLimits = makeUpdater(volumeLimits);
  const updateVolumeStartup = makeUpdater(volumeStartup);
  const updateVolumeSteps = makeUpdater(volumeSteps);

  /**
   * Update dock apps (from WebSocket or API response)
   */
  function updateDockApps(enabledApps) {
    dockApps.value = {
      spotify: enabledApps.includes('spotify'),
      bluetooth: enabledApps.includes('bluetooth'),
      radio: enabledApps.includes('radio'),
      podcast: enabledApps.includes('podcast'),
      airplay: enabledApps.includes('airplay'),
      mac: enabledApps.includes('mac'),
      equalizer: enabledApps.includes('equalizer'),
      multiroom: enabledApps.includes('multiroom'),
      settings: enabledApps.includes('settings')
    };
    syncSourceOrder(enabledApps);
  }

  /**
   * Extract audio source order from enabled_apps array.
   * Preserves order of audio sources from the array, appends any missing sources at the end.
   */
  function syncSourceOrder(enabledApps) {
    const audioFromServer = enabledApps.filter(a => ALL_AUDIO_SOURCES.includes(a));
    const enabledSet = new Set(audioFromServer);
    let i = 0;
    sourceOrder.value = sourceOrder.value.map(s => enabledSet.has(s) ? audioFromServer[i++] : s);
  }

  /**
   * Update source display order
   */
  function updateSourceOrder(newOrder) {
    sourceOrder.value = [...newOrder];
  }

  /**
   * Build the full enabled_apps array preserving source order.
   * Returns ordered enabled audio sources followed by enabled utility apps.
   */
  function buildEnabledAppsArray() {
    const orderedAudio = sourceOrder.value.filter(s => dockApps.value[s]);
    const utilities = ['equalizer', 'multiroom', 'settings'].filter(u => dockApps.value[u]);
    return [...orderedAudio, ...utilities];
  }

  const updateSpotifyDisconnect = makeUpdater(spotifyDisconnect);
  const updatePodcastCredentials = makeUpdater(podcastCredentials);

  /**
   * Refresh podcast credentials status (after validation/save)
   */
  async function refreshPodcastCredentialsStatus() {
    await apiCall('settings', 'Error refreshing podcast credentials status:', async () => {
      const response = await axios.get('/api/settings/podcast-credentials/status');
      podcastCredentialsStatus.value = response.data.status ?? 'error';
      podcastApiUsage.value = response.data.requests_used ?? null;
      podcastCredentialsValidatedAt.value = response.data.credentials_validated_at ?? null;
    });
  }

  /**
   * Update screen sleep state (from WebSocket broadcast)
   */
  function updateScreenSleeping(sleeping) {
    isScreenSleeping.value = sleeping;
  }

  const updateScreenTimeout = makeUpdater(screenTimeout);
  const updateScreenBrightness = makeUpdater(screenBrightness);
  const updateScreenScreensaver = makeUpdater(screenScreensaver);
  const updateInactivityTimeout = makeUpdater(inactivityTimeout);
  const updateRadioSettings = makeUpdater(radioSettings);

  return {
    // State
    isLoading,
    hasLoaded,
    language,
    volumeLimits,
    volumeStartup,
    volumeSteps,
    dockApps,
    sourceOrder,
    spotifyDisconnect,
    podcastCredentials,
    podcastCredentialsStatus,
    podcastApiUsage,
    podcastCredentialsValidatedAt,
    inactivityTimeout,
    radioSettings,
    isScreenSleeping,
    screenTimeout,
    screenBrightness,
    screenScreensaver,

    // Actions
    loadAllSettings,
    updateLanguage,
    updateVolumeLimits,
    updateVolumeStartup,
    updateVolumeSteps,
    updateDockApps,
    updateSourceOrder,
    buildEnabledAppsArray,
    updateSpotifyDisconnect,
    updatePodcastCredentials,
    refreshPodcastCredentialsStatus,
    updateInactivityTimeout,
    updateRadioSettings,
    updateScreenSleeping,
    updateScreenTimeout,
    updateScreenBrightness,
    updateScreenScreensaver
  };
});
