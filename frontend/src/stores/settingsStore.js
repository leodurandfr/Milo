// frontend/src/stores/settingsStore.js
import { defineStore } from 'pinia';
import { ref } from 'vue';
import axios from 'axios';

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
  const dockApps = ref({
    spotify: true,
    bluetooth: true,
    mac: true,
    radio: true,
    podcast: true,
    multiroom: true,
    dsp: true,
    settings: true
  });

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

  // === RADIO ===
  const radioSettings = ref({
    shazam_enabled: true
  });

  // === SCREEN ===
  const screenTimeout = ref({
    screen_timeout_enabled: true,
    screen_timeout_seconds: 10
  });

  const screenBrightness = ref({
    brightness_on: 5
  });

  // === ACTIONS ===

  /**
   * Load all settings in parallel
   */
  async function loadAllSettings() {
    if (isLoading.value) return;

    isLoading.value = true;
    try {
      // Note: step_mobile_db comes from unifiedAudioStore.volumeState via WebSocket initial_state
      const [
        langResponse,
        volumeLimitsResponse,
        volumeStartupResponse,
        rotaryStepsResponse,
        dockAppsResponse,
        spotifyResponse,
        podcastResponse,
        podcastStatusResponse,
        screenTimeoutResponse,
        screenBrightnessResponse,
        radioSettingsResponse
      ] = await Promise.all([
        axios.get('/api/settings/language').catch(() => ({ data: { language: 'english' } })),
        axios.get('/api/settings/volume-limits').catch(() => ({ data: { limits: { min_db: -80.0, max_db: -21.0 } } })),
        axios.get('/api/settings/volume-startup').catch(() => ({ data: { config: { startup_volume_db: -30.0, restore_last_volume: false } } })),
        axios.get('/api/settings/rotary-steps').catch(() => ({ data: { config: { step_rotary_db: 2.0 } } })),
        axios.get('/api/settings/dock-apps').catch(() => ({ data: { config: { enabled_apps: ['spotify', 'bluetooth', 'mac', 'radio', 'podcast', 'multiroom', 'dsp', 'settings'] } } })),
        axios.get('/api/settings/spotify-disconnect').catch(() => ({ data: { config: { auto_disconnect_delay: 10.0 } } })),
        axios.get('/api/settings/podcast-credentials').catch(() => ({ data: { config: { taddy_user_id: '', taddy_api_key: '' } } })),
        axios.get('/api/settings/podcast-credentials/status').catch(() => ({ data: { status: 'error' } })),
        axios.get('/api/settings/screen-timeout').catch(() => ({ data: { config: { screen_timeout_enabled: true, screen_timeout_seconds: 10 } } })),
        axios.get('/api/settings/screen-brightness').catch(() => ({ data: { config: { brightness_on: 5 } } })),
        axios.get('/api/settings/radio-settings').catch(() => ({ data: { config: { shazam_enabled: true } } }))
      ]);

      // Language
      if (langResponse.data.language) {
        language.value = langResponse.data.language;
      }

      // Volume limits (in dB)
      if (volumeLimitsResponse.data.limits) {
        volumeLimits.value = {
          min_db: volumeLimitsResponse.data.limits.min_db ?? -80.0,
          max_db: volumeLimitsResponse.data.limits.max_db ?? -21.0
        };
      }

      // Volume startup (in dB)
      if (volumeStartupResponse.data.config) {
        volumeStartup.value = {
          startup_volume_db: volumeStartupResponse.data.config.startup_volume_db ?? -30.0,
          restore_last_volume: volumeStartupResponse.data.config.restore_last_volume ?? false
        };
      }

      // Note: step_mobile_db comes from unifiedAudioStore.volumeState via WebSocket
      // No need to load it here

      // Rotary steps (in dB)
      if (rotaryStepsResponse.data.config) {
        volumeSteps.value.step_rotary_db = rotaryStepsResponse.data.config.step_rotary_db ?? 2.0;
      }

      // Dock apps
      if (dockAppsResponse.data.config?.enabled_apps) {
        const enabledApps = dockAppsResponse.data.config.enabled_apps;
        dockApps.value = {
          spotify: enabledApps.includes('spotify'),
          bluetooth: enabledApps.includes('bluetooth'),
          mac: enabledApps.includes('mac'),
          radio: enabledApps.includes('radio'),
          podcast: enabledApps.includes('podcast'),
          multiroom: enabledApps.includes('multiroom'),
          dsp: enabledApps.includes('dsp'),
          settings: enabledApps.includes('settings')
        };
      }

      // Spotify
      if (spotifyResponse.data.config) {
        spotifyDisconnect.value = {
          auto_disconnect_delay: spotifyResponse.data.config.auto_disconnect_delay ?? 10.0
        };
      }

      // Podcast credentials
      if (podcastResponse.data.config) {
        podcastCredentials.value = {
          taddy_user_id: podcastResponse.data.config.taddy_user_id ?? '',
          taddy_api_key: podcastResponse.data.config.taddy_api_key ?? ''
        };
      }

      // Podcast credentials status
      if (podcastStatusResponse.data) {
        podcastCredentialsStatus.value = podcastStatusResponse.data.status ?? 'error';
        podcastApiUsage.value = podcastStatusResponse.data.requests_used ?? null;
        podcastCredentialsValidatedAt.value = podcastStatusResponse.data.credentials_validated_at ?? null;
      }

      // Screen timeout
      if (screenTimeoutResponse.data.config) {
        screenTimeout.value = {
          screen_timeout_enabled: screenTimeoutResponse.data.config.screen_timeout_enabled ?? true,
          screen_timeout_seconds: screenTimeoutResponse.data.config.screen_timeout_seconds ?? 10
        };
      }

      // Screen brightness
      if (screenBrightnessResponse.data.config) {
        screenBrightness.value = {
          brightness_on: screenBrightnessResponse.data.config.brightness_on ?? 5
        };
      }

      // Radio settings
      if (radioSettingsResponse.data.config) {
        radioSettings.value = {
          shazam_enabled: radioSettingsResponse.data.config.shazam_enabled ?? true
        };
      }

      hasLoaded.value = true;
      console.log('✅ All settings loaded successfully');

    } catch (error) {
      console.error('❌ Error loading settings:', error);
    } finally {
      isLoading.value = false;
    }
  }

  /**
   * Update language
   */
  function updateLanguage(newLanguage) {
    language.value = newLanguage;
  }

  /**
   * Update volume limits (in dB)
   */
  function updateVolumeLimits(limits) {
    volumeLimits.value = { ...volumeLimits.value, ...limits };
  }

  /**
   * Update startup volume (in dB)
   */
  function updateVolumeStartup(config) {
    volumeStartup.value = { ...volumeStartup.value, ...config };
  }

  /**
   * Update volume steps (in dB)
   */
  function updateVolumeSteps(steps) {
    volumeSteps.value = { ...volumeSteps.value, ...steps };
  }

  /**
   * Update dock apps
   */
  function updateDockApps(enabledApps) {
    dockApps.value = {
      spotify: enabledApps.includes('spotify'),
      bluetooth: enabledApps.includes('bluetooth'),
      mac: enabledApps.includes('mac'),
      radio: enabledApps.includes('radio'),
      podcast: enabledApps.includes('podcast'),
      multiroom: enabledApps.includes('multiroom'),
      dsp: enabledApps.includes('dsp'),
      settings: enabledApps.includes('settings')
    };
  }

  /**
   * Update Spotify config
   */
  function updateSpotifyDisconnect(config) {
    spotifyDisconnect.value = { ...spotifyDisconnect.value, ...config };
  }

  /**
   * Update podcast credentials
   */
  function updatePodcastCredentials(config) {
    podcastCredentials.value = { ...podcastCredentials.value, ...config };
  }

  /**
   * Refresh podcast credentials status (after validation/save)
   */
  async function refreshPodcastCredentialsStatus() {
    try {
      const response = await axios.get('/api/settings/podcast-credentials/status');
      podcastCredentialsStatus.value = response.data.status ?? 'error';
      podcastApiUsage.value = response.data.requests_used ?? null;
      podcastCredentialsValidatedAt.value = response.data.credentials_validated_at ?? null;
    } catch (error) {
      console.error('Error refreshing podcast credentials status:', error);
      podcastCredentialsStatus.value = 'error';
      podcastApiUsage.value = null;
      podcastCredentialsValidatedAt.value = null;
    }
  }

  /**
   * Update screen timeout
   */
  function updateScreenTimeout(config) {
    screenTimeout.value = { ...screenTimeout.value, ...config };
  }

  /**
   * Update screen brightness
   */
  function updateScreenBrightness(config) {
    screenBrightness.value = { ...screenBrightness.value, ...config };
  }

  /**
   * Update radio settings
   */
  function updateRadioSettings(config) {
    radioSettings.value = { ...radioSettings.value, ...config };
  }

  return {
    // State
    isLoading,
    hasLoaded,
    language,
    volumeLimits,
    volumeStartup,
    volumeSteps,
    dockApps,
    spotifyDisconnect,
    podcastCredentials,
    podcastCredentialsStatus,
    podcastApiUsage,
    podcastCredentialsValidatedAt,
    radioSettings,
    screenTimeout,
    screenBrightness,

    // Actions
    loadAllSettings,
    updateLanguage,
    updateVolumeLimits,
    updateVolumeStartup,
    updateVolumeSteps,
    updateDockApps,
    updateSpotifyDisconnect,
    updatePodcastCredentials,
    refreshPodcastCredentialsStatus,
    updateRadioSettings,
    updateScreenTimeout,
    updateScreenBrightness
  };
});
