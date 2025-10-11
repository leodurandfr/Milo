// frontend/src/stores/settingsStore.js
import { defineStore } from 'pinia';
import { ref } from 'vue';
import axios from 'axios';

export const useSettingsStore = defineStore('settings', () => {
  // === LOADING STATE ===
  const isLoading = ref(false);
  const hasLoaded = ref(false);

  // === LANGUAGE ===
  const language = ref('french');

  // === VOLUME ===
  const volumeLimits = ref({
    alsa_min: 0,
    alsa_max: 65,
    limits_enabled: true
  });

  const volumeStartup = ref({
    startup_volume: 37,
    restore_last_volume: false
  });

  const volumeSteps = ref({
    mobile_volume_steps: 5,
    rotary_volume_steps: 2
  });

  // === DOCK APPS ===
  const dockApps = ref({
    librespot: true,
    bluetooth: true,
    roc: true,
    multiroom: true,
    equalizer: true,
    settings: true
  });

  // === SPOTIFY ===
  const spotifyDisconnect = ref({
    auto_disconnect_delay: 10.0
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
   * Charge tous les settings en parallèle
   */
  async function loadAllSettings() {
    if (isLoading.value) return;

    isLoading.value = true;
    try {
      const [
        langResponse,
        volumeLimitsResponse,
        volumeStartupResponse,
        volumeStepsResponse,
        rotaryStepsResponse,
        dockAppsResponse,
        spotifyResponse,
        screenTimeoutResponse,
        screenBrightnessResponse
      ] = await Promise.all([
        axios.get('/api/settings/language').catch(() => ({ data: { language: 'french' } })),
        axios.get('/api/settings/volume-limits').catch(() => ({ data: { limits: { alsa_min: 0, alsa_max: 65, limits_enabled: true } } })),
        axios.get('/api/settings/volume-startup').catch(() => ({ data: { config: { startup_volume: 37, restore_last_volume: false } } })),
        axios.get('/api/settings/volume-steps').catch(() => ({ data: { config: { mobile_volume_steps: 5 } } })),
        axios.get('/api/settings/rotary-steps').catch(() => ({ data: { config: { rotary_volume_steps: 2 } } })),
        axios.get('/api/settings/dock-apps').catch(() => ({ data: { config: { enabled_apps: ['librespot', 'bluetooth', 'roc', 'multiroom', 'equalizer', 'settings'] } } })),
        axios.get('/api/settings/spotify-disconnect').catch(() => ({ data: { config: { auto_disconnect_delay: 10.0 } } })),
        axios.get('/api/settings/screen-timeout').catch(() => ({ data: { config: { screen_timeout_enabled: true, screen_timeout_seconds: 10 } } })),
        axios.get('/api/settings/screen-brightness').catch(() => ({ data: { config: { brightness_on: 5 } } }))
      ]);

      // Language
      if (langResponse.data.language) {
        language.value = langResponse.data.language;
      }

      // Volume limits
      if (volumeLimitsResponse.data.limits) {
        volumeLimits.value = {
          alsa_min: volumeLimitsResponse.data.limits.alsa_min ?? 0,
          alsa_max: volumeLimitsResponse.data.limits.alsa_max ?? 65,
          limits_enabled: volumeLimitsResponse.data.limits.limits_enabled ?? true
        };
      }

      // Volume startup
      if (volumeStartupResponse.data.config) {
        volumeStartup.value = {
          startup_volume: volumeStartupResponse.data.config.startup_volume ?? 37,
          restore_last_volume: volumeStartupResponse.data.config.restore_last_volume ?? false
        };
      }

      // Volume steps (mobile)
      if (volumeStepsResponse.data.config) {
        volumeSteps.value.mobile_volume_steps = volumeStepsResponse.data.config.mobile_volume_steps ?? 5;
      }

      // Rotary steps
      if (rotaryStepsResponse.data.config) {
        volumeSteps.value.rotary_volume_steps = rotaryStepsResponse.data.config.rotary_volume_steps ?? 2;
      }

      // Dock apps
      if (dockAppsResponse.data.config?.enabled_apps) {
        const enabledApps = dockAppsResponse.data.config.enabled_apps;
        dockApps.value = {
          librespot: enabledApps.includes('librespot'),
          bluetooth: enabledApps.includes('bluetooth'),
          roc: enabledApps.includes('roc'),
          multiroom: enabledApps.includes('multiroom'),
          equalizer: enabledApps.includes('equalizer'),
          settings: enabledApps.includes('settings')
        };
      }

      // Spotify
      if (spotifyResponse.data.config) {
        spotifyDisconnect.value = {
          auto_disconnect_delay: spotifyResponse.data.config.auto_disconnect_delay ?? 10.0
        };
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

      hasLoaded.value = true;
      console.log('✅ All settings loaded successfully');

    } catch (error) {
      console.error('❌ Error loading settings:', error);
    } finally {
      isLoading.value = false;
    }
  }

  /**
   * Met à jour la langue
   */
  function updateLanguage(newLanguage) {
    language.value = newLanguage;
  }

  /**
   * Met à jour les limites de volume
   */
  function updateVolumeLimits(limits) {
    volumeLimits.value = { ...volumeLimits.value, ...limits };
  }

  /**
   * Met à jour le volume au démarrage
   */
  function updateVolumeStartup(config) {
    volumeStartup.value = { ...volumeStartup.value, ...config };
  }

  /**
   * Met à jour les steps de volume (mobile et/ou rotary)
   */
  function updateVolumeSteps(steps) {
    volumeSteps.value = { ...volumeSteps.value, ...steps };
  }

  /**
   * Met à jour les apps du dock
   */
  function updateDockApps(enabledApps) {
    dockApps.value = {
      librespot: enabledApps.includes('librespot'),
      bluetooth: enabledApps.includes('bluetooth'),
      roc: enabledApps.includes('roc'),
      multiroom: enabledApps.includes('multiroom'),
      equalizer: enabledApps.includes('equalizer'),
      settings: enabledApps.includes('settings')
    };
  }

  /**
   * Met à jour la config Spotify
   */
  function updateSpotifyDisconnect(config) {
    spotifyDisconnect.value = { ...spotifyDisconnect.value, ...config };
  }

  /**
   * Met à jour le timeout de l'écran
   */
  function updateScreenTimeout(config) {
    screenTimeout.value = { ...screenTimeout.value, ...config };
  }

  /**
   * Met à jour la luminosité de l'écran
   */
  function updateScreenBrightness(config) {
    screenBrightness.value = { ...screenBrightness.value, ...config };
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
    updateScreenTimeout,
    updateScreenBrightness
  };
});
