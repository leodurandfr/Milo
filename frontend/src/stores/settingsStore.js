// frontend/src/stores/settingsStore.js
import { defineStore } from 'pinia';
import { ref } from 'vue';
import { logger } from '@/services/logger';
import { apiCall } from '@/services/apiCall';
import { ALL_AUDIO_SOURCES } from '@/constants/audioSources';

export const useSettingsStore = defineStore('settings', () => {
  // === SETUP WIZARD ===
  const setupCompleted = ref(null); // null = unknown, false = show wizard, true = normal UI
  const hotspotActive = ref(false); // true when first-boot hotspot is running

  // === LOADING STATE ===
  const isLoading = ref(false);
  const hasLoaded = ref(false);

  // === LANGUAGE ===
  const language = ref('english');

  // === VOLUME (all values in dB) ===
  const volumeLimits = ref({
    min_db: -80.0,
    max_db: -20.0
  });

  const volumeStartup = ref({
    startup_volume_db: -45.0,
    restore_last_volume: true
  });

  // Note: step_mobile_db is in unifiedAudioStore.volumeState (single source of truth)
  // Only step_rotary_db is kept here as it's hardware-specific
  const volumeSteps = ref({
    step_rotary_db: 2.0,
    step_bt_remote_db: 2.0,
    step_ir_remote_db: 2.0
  });

  // === DOCK APPS ===
  const dockApps = ref({
    spotify: true,
    bluetooth: true,
    radio: true,
    podcast: true,
    airplay: true,
    mac: true,
    cd: true,
    equalizer: true,
    multiroom: true,
    settings: true
  });

  // Ordered list of all audio sources (both enabled and disabled)
  const sourceOrder = ref([...ALL_AUDIO_SOURCES]);

  // === PODCAST ===
  const podcastCredentials = ref({
    taddy_user_id: '',
    taddy_api_key: ''
  });

  // Podcast credentials status (checked at startup)
  const podcastCredentialsStatus = ref('unknown'); // 'unknown', 'valid', 'missing', 'invalid', 'rate_limited', 'error'
  const podcastApiUsage = ref(null); // requests_used (null if no valid credentials)
  const podcastCredentialsValidatedAt = ref(null); // Unix timestamp when credentials were validated

  // === AUDIO PLAYBACK ===
  // Global behavior applied to every eligible audio source:
  // - auto_stop_delay: stop a paused (or silent) source after N seconds (0 = disabled)
  const audioPlayback = ref({
    auto_stop_delay: 120.0
  });

  // === RADIO ===
  const radioSettings = ref({
    shazam_enabled: true
  });

  // === MAC ROC ===
  const macRocSettings = ref({
    target_latency_ms: 50,
    latency_profile: 'responsive',
    frame_length_ms: 4
  });

  // === BT REMOTE ===
  const btRemote = ref({
    enabled: false,
    connected: false,
    discovering: false,
    device_name: '',
    battery_percentage: null
  });

  // === IR REMOTE ===
  const irRemote = ref({
    available: true,
    enabled: false,
    paired: false,
    device_id: null,
    paired_at: null,
    listening: false,
    pairing_in_progress: false
  });

  // === SCREEN ===
  const isScreenSleeping = ref(false);

  const screenTimeout = ref({
    screen_timeout_enabled: true,
    screen_timeout_seconds: 120
  });

  const screenBrightness = ref({
    brightness_on: 5
  });

  const screenScreensaver = ref({
    screensaver_enabled: true,
    screensaver_delay_seconds: 120
  });

  const screenUiScale = ref({
    ui_scale: 1.0
  });

  const screenColorFilter = ref({
    enabled: false,
    warmth: 50
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
    try {
      const bulkResult = await apiCall.get('/api/settings/bulk', {
        category: 'settings',
        message: 'Error loading settings',
      });

      const d = bulkResult.ok ? bulkResult.data : null;
      if (d) {
        language.value = d.language ?? 'english';

        volumeLimits.value = {
          min_db: d.volume_limits?.min_db ?? -80.0,
          max_db: d.volume_limits?.max_db ?? -20.0
        };

        volumeStartup.value = {
          startup_volume_db: d.volume_startup?.startup_volume_db ?? -45.0,
          restore_last_volume: d.volume_startup?.restore_last_volume ?? true
        };

        volumeSteps.value.step_rotary_db = d.rotary_steps?.step_rotary_db ?? 2.0;
        volumeSteps.value.step_bt_remote_db = d.bt_remote_steps?.step_bt_remote_db ?? 2.0;
        volumeSteps.value.step_ir_remote_db = d.ir_remote_steps?.step_ir_remote_db ?? 2.0;

        if (d.dock_apps?.enabled_apps) {
          const enabledApps = d.dock_apps.enabled_apps;
          dockApps.value = {
            spotify: enabledApps.includes('spotify'),
            bluetooth: enabledApps.includes('bluetooth'),
            radio: enabledApps.includes('radio'),
            podcast: enabledApps.includes('podcast'),
            airplay: enabledApps.includes('airplay'),
            mac: enabledApps.includes('mac'),
            cd: enabledApps.includes('cd'),
            equalizer: enabledApps.includes('equalizer'),
            multiroom: enabledApps.includes('multiroom'),
            settings: enabledApps.includes('settings')
          };
          syncSourceOrder(enabledApps);
        }

        podcastCredentials.value = {
          taddy_user_id: d.podcast_credentials?.taddy_user_id ?? '',
          taddy_api_key: d.podcast_credentials?.taddy_api_key ?? ''
        };

        audioPlayback.value = {
          auto_stop_delay: d.audio_stop?.auto_stop_delay ?? 120.0
        };

        screenTimeout.value = {
          screen_timeout_enabled: d.screen_timeout?.screen_timeout_enabled ?? true,
          screen_timeout_seconds: d.screen_timeout?.screen_timeout_seconds ?? 120
        };

        screenBrightness.value = {
          brightness_on: d.screen_brightness?.brightness_on ?? 5
        };

        screenScreensaver.value = {
          screensaver_enabled: d.screen_screensaver?.screensaver_enabled ?? true,
          screensaver_delay_seconds: d.screen_screensaver?.screensaver_delay_seconds ?? 120
        };

        screenUiScale.value = {
          ui_scale: d.screen_ui_scale?.ui_scale ?? 1.0
        };
        applyUiScale(screenUiScale.value.ui_scale);

        screenColorFilter.value = {
          enabled: d.screen_color_filter?.enabled ?? false,
          warmth: d.screen_color_filter?.warmth ?? 50
        };

        radioSettings.value = {
          shazam_enabled: d.radio_settings?.shazam_enabled ?? true
        };

        macRocSettings.value = {
          target_latency_ms: d.mac_roc?.target_latency_ms ?? 50,
          latency_profile: d.mac_roc?.latency_profile ?? 'responsive',
          frame_length_ms: d.mac_roc?.frame_length_ms ?? 4
        };
      }

      hasLoaded.value = true;
      logger.info('settings', 'All settings loaded successfully');
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
      cd: enabledApps.includes('cd'),
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

  const updateAudioPlayback = makeUpdater(audioPlayback);
  const updatePodcastCredentials = makeUpdater(podcastCredentials);

  /**
   * Refresh podcast credentials status (after validation/save)
   */
  async function refreshPodcastCredentialsStatus() {
    const result = await apiCall.get('/api/settings/podcast-credentials/status', {
      category: 'settings',
      message: 'Error refreshing podcast credentials status',
    });
    if (result.ok) {
      podcastCredentialsStatus.value = result.data.status ?? 'error';
      podcastApiUsage.value = result.data.requests_used ?? null;
      podcastCredentialsValidatedAt.value = result.data.credentials_validated_at ?? null;
    }
  }

  // === BT REMOTE ACTIONS ===

  function updateBtRemoteConfig(config) {
    if (config.enabled !== undefined) btRemote.value.enabled = config.enabled;
  }

  function updateBtRemoteStatus(data) {
    const devices = data.connected_devices || [];
    const wasConnected = btRemote.value.connected;
    btRemote.value.connected = devices.length > 0;
    btRemote.value.device_name = devices[0]?.name || '';
    if (devices.length === 0) btRemote.value.battery_percentage = null;
    if (data.discovering !== undefined) btRemote.value.discovering = data.discovering;
    // Fetch battery immediately when a device just connected
    if (!wasConnected && devices.length > 0) {
      fetchBtRemoteBattery();
    }
  }

  async function loadBtRemoteStatus() {
    const result = await apiCall.get('/api/bt-remote/status', {
      category: 'settings',
      message: 'Error loading BT remote status',
    });
    if (result.ok) {
      btRemote.value.enabled = result.data.enabled ?? false;
      updateBtRemoteStatus(result.data);
    }
  }

  async function toggleBtRemote(enabled) {
    const prev = { ...btRemote.value };
    btRemote.value.enabled = enabled;
    if (enabled) {
      btRemote.value.connected = false;
      btRemote.value.device_name = '';
      btRemote.value.discovering = true;
    }
    const result = await apiCall.patch('/api/bt-remote/config', { enabled }, {
      category: 'settings',
      message: 'Error toggling BT remote',
    });
    if (!result.ok) {
      Object.assign(btRemote.value, prev);
    }
  }

  async function fetchBtRemoteBattery() {
    const result = await apiCall.get('/api/bt-remote/battery', {
      category: 'settings',
      message: 'Error fetching BT remote battery',
    });
    if (result.ok) {
      const devices = result.data.devices || [];
      btRemote.value.battery_percentage = devices[0]?.battery_percentage ?? null;
    }
  }

  async function discoverBtRemote() {
    btRemote.value.discovering = true;
    const result = await apiCall.post('/api/bt-remote/discover', null, {
      category: 'settings',
      message: 'Error discovering BT remote',
    });
    if (!result.ok) {
      btRemote.value.discovering = false;
      return false;
    }
    return result.data.status;
  }

  // === IR REMOTE ACTIONS ===

  function applyIrRemoteStatus(data) {
    if (data.available !== undefined) irRemote.value.available = data.available;
    if (data.enabled !== undefined) irRemote.value.enabled = data.enabled;
    if (data.paired !== undefined) irRemote.value.paired = data.paired;
    if (data.device_id !== undefined) irRemote.value.device_id = data.device_id;
    if (data.paired_at !== undefined) irRemote.value.paired_at = data.paired_at;
    if (data.listening !== undefined) irRemote.value.listening = data.listening;
    if (data.pairing_in_progress !== undefined) {
      irRemote.value.pairing_in_progress = data.pairing_in_progress;
    }
  }

  async function loadIrRemoteStatus() {
    const result = await apiCall.get('/api/ir-remote/status', {
      category: 'settings',
      message: 'Error loading IR remote status',
    });
    if (result.ok) {
      applyIrRemoteStatus(result.data);
    }
  }

  async function toggleIrRemote(enabled) {
    const prev = { ...irRemote.value };
    irRemote.value.enabled = enabled;
    const result = await apiCall.patch('/api/ir-remote/config', { enabled }, {
      category: 'settings',
      message: 'Error toggling IR remote',
    });
    if (result.ok) {
      applyIrRemoteStatus(result.data);
    } else {
      Object.assign(irRemote.value, prev);
    }
  }

  /**
   * Start the pairing capture. Resolves with the backend's pairing result:
   *   { status: 'success' | 'timeout' | 'cancelled' | 'unsupported' | 'error',
   *     device_id?: number, message?: string }
   */
  async function startIrRemotePairing() {
    const result = await apiCall.post('/api/ir-remote/pair', null, {
      category: 'settings',
      message: 'Error starting IR pairing',
    });
    return result.ok ? result.data : false;
  }

  async function cancelIrRemotePairing() {
    const result = await apiCall.post('/api/ir-remote/pair/cancel', null, {
      category: 'settings',
      message: 'Error cancelling IR pairing',
    });
    return result.ok ? result.data : false;
  }

  async function unpairIrRemote() {
    const result = await apiCall.delete('/api/ir-remote/pair', {
      category: 'settings',
      message: 'Error unpairing IR remote',
    });
    if (result.ok) {
      applyIrRemoteStatus(result.data);
    }
    return result.ok;
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
  const updateScreenColorFilter = makeUpdater(screenColorFilter);

  function updateScreenUiScale(config) {
    screenUiScale.value = { ...screenUiScale.value, ...config };
    applyUiScale(screenUiScale.value.ui_scale);
  }

  function applyUiScale(scale) {
    const isKiosk = window.location.hostname === 'localhost';
    const appEl = document.getElementById('app');
    if (!appEl) return;
    if (!isKiosk || scale === 1.0) {
      appEl.style.transform = '';
      appEl.style.transformOrigin = '';
      appEl.style.width = '';
      appEl.style.height = '';
      appEl.style.overflow = '';
    } else {
      appEl.style.transform = `scale(${scale})`;
      appEl.style.transformOrigin = 'top left';
      appEl.style.width = `calc(100vw / ${scale})`;
      appEl.style.height = `calc(100vh / ${scale})`;
      appEl.style.overflow = 'hidden';
    }
  }
  const updateRadioSettings = makeUpdater(radioSettings);
  const updateMacRocSettings = makeUpdater(macRocSettings);

  /**
   * Update setup_completed state (from WebSocket initial_state)
   */
  function updateSetupCompleted(value) {
    setupCompleted.value = value;
  }

  function updateHotspotActive(value) {
    hotspotActive.value = value;
  }

  return {
    // State
    setupCompleted,
    hotspotActive,
    isLoading,
    hasLoaded,
    language,
    volumeLimits,
    volumeStartup,
    volumeSteps,
    dockApps,
    sourceOrder,
    audioPlayback,
    podcastCredentials,
    podcastCredentialsStatus,
    podcastApiUsage,
    podcastCredentialsValidatedAt,
    radioSettings,
    macRocSettings,
    btRemote,
    irRemote,
    isScreenSleeping,
    screenTimeout,
    screenBrightness,
    screenScreensaver,
    screenUiScale,
    screenColorFilter,

    // Actions
    loadAllSettings,
    updateSetupCompleted,
    updateHotspotActive,
    updateLanguage,
    updateVolumeLimits,
    updateVolumeStartup,
    updateVolumeSteps,
    updateDockApps,
    updateSourceOrder,
    buildEnabledAppsArray,
    updateAudioPlayback,
    updatePodcastCredentials,
    refreshPodcastCredentialsStatus,
    updateRadioSettings,
    updateMacRocSettings,
    updateBtRemoteConfig,
    updateBtRemoteStatus,
    loadBtRemoteStatus,
    toggleBtRemote,
    fetchBtRemoteBattery,
    discoverBtRemote,
    applyIrRemoteStatus,
    loadIrRemoteStatus,
    toggleIrRemote,
    startIrRemotePairing,
    cancelIrRemotePairing,
    unpairIrRemote,
    updateScreenSleeping,
    updateScreenTimeout,
    updateScreenBrightness,
    updateScreenColorFilter,
    updateScreenScreensaver,
    updateScreenUiScale
  };
});
