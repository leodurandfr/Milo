// frontend/src/stores/settingsStore.js
import { defineStore } from 'pinia';
import { ref } from 'vue';
import { logger } from '@/services/logger';
import { apiCall } from '@/services/apiCall';
import { ALL_AUDIO_SOURCES } from '@/constants/audioSources';
import { isKiosk } from '@/utils/kiosk';

// Non-source dock apps; the full dock roster is sources + utilities.
const DOCK_UTILITY_APPS = ['equalizer', 'multiroom', 'lyrics', 'settings'];

function buildDockAppsMap(enabledApps) {
  return Object.fromEntries(
    [...ALL_AUDIO_SOURCES, ...DOCK_UTILITY_APPS].map(app => [app, enabledApps.includes(app)])
  );
}

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
  const dockApps = ref(
    Object.fromEntries([...ALL_AUDIO_SOURCES, ...DOCK_UTILITY_APPS].map(app => [app, true]))
  );

  // Ordered list of all audio sources (both enabled and disabled)
  const sourceOrder = ref([...ALL_AUDIO_SOURCES]);

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

  // === QOBUZ ===
  // allow_app_volume=false → qobuz-proxy stays at unity (CamillaDSP owns volume),
  // the Qobuz app slider is inert; true → the app slider controls qobuz volume.
  const qobuzSettings = ref({
    allow_app_volume: false
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
    // Durable BlueZ bond — stays true while the remote sleeps/disconnects.
    // Drives the "Unpair" action (connected is too transient for that).
    paired: false,
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
   * Assign a config ref only when its content actually changed. loadAllSettings
   * re-runs on every reconnect/tab-visible resync; a fresh object with identical
   * values would retrigger watchers (e.g. DockSettings watches sourceOrder and a
   * same-content reassign would reset an in-progress dock reorder).
   */
  function setIfChanged(target, value) {
    if (JSON.stringify(target.value) !== JSON.stringify(value)) {
      target.value = value;
    }
  }

  /**
   * Load all settings in parallel. Single-flight: concurrent callers share
   * the in-flight request and all await its completion.
   */
  let loadAllPromise = null;

  function loadAllSettings() {
    if (loadAllPromise) return loadAllPromise;
    loadAllPromise = doLoadAllSettings();
    return loadAllPromise;
  }

  async function doLoadAllSettings() {
    isLoading.value = true;
    try {
      const bulkResult = await apiCall.get('/api/settings/bulk', {
        category: 'settings',
        message: 'Error loading settings',
      });

      // BulkSettingsResponse declares every category as a required field, so a
      // 200 carries them all: no key needs a fallback, and restating one here
      // would be a second declaration of a default the backend owns.
      const d = bulkResult.ok ? bulkResult.data : null;
      if (d) {
        language.value = d.language;

        setIfChanged(volumeLimits, d.volume_limits);
        setIfChanged(volumeStartup, d.volume_startup);

        // The three hardware step sizes are separate categories on the wire and
        // one ref here (step_mobile_db joins them from volume_changed).
        volumeSteps.value.step_rotary_db = d.rotary_steps.step_rotary_db;
        volumeSteps.value.step_bt_remote_db = d.bt_remote_steps.step_bt_remote_db;
        volumeSteps.value.step_ir_remote_db = d.ir_remote_steps.step_ir_remote_db;

        const enabledApps = d.dock_apps.enabled_apps;
        setIfChanged(dockApps, buildDockAppsMap(enabledApps));
        syncSourceOrder(enabledApps);

        setIfChanged(audioPlayback, d.audio_stop);
        setIfChanged(screenTimeout, d.screen_timeout);
        setIfChanged(screenBrightness, d.screen_brightness);
        setIfChanged(screenScreensaver, d.screen_screensaver);

        setIfChanged(screenUiScale, d.screen_ui_scale);
        applyUiScale(screenUiScale.value.ui_scale);

        setIfChanged(screenColorFilter, d.screen_color_filter);
        setIfChanged(radioSettings, d.radio_settings);
        setIfChanged(qobuzSettings, d.qobuz_settings);
        setIfChanged(macRocSettings, d.mac_roc);
      }

      hasLoaded.value = true;
      logger.info('settings', 'All settings loaded successfully');
    } finally {
      isLoading.value = false;
      loadAllPromise = null;
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
    dockApps.value = buildDockAppsMap(enabledApps);
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
    // setIfChanged: a same-content reassign would reset an in-progress dock
    // reorder (DockSettings watches sourceOrder) on every resync
    setIfChanged(sourceOrder, sourceOrder.value.map(s => enabledSet.has(s) ? audioFromServer[i++] : s));
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
    const utilities = DOCK_UTILITY_APPS.filter(u => dockApps.value[u]);
    return [...orderedAudio, ...utilities];
  }

  const updateAudioPlayback = makeUpdater(audioPlayback);

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
    if (data.paired !== undefined) btRemote.value.paired = data.paired;
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
    // Disabling keeps the BlueZ bond (only the explicit "unpair" removes it), so
    // `paired` persists across a toggle and is driven solely by the backend
    // status broadcast / unpairBtRemote().
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

  async function unpairBtRemote() {
    const result = await apiCall.delete('/api/bt-remote/pairing', {
      category: 'settings',
      message: 'Error unpairing BT remote',
    });
    if (result.ok) {
      // Backend also broadcasts the new status via WS, but clear optimistically
      // so the "Unpair" button hides immediately.
      btRemote.value.paired = false;
      btRemote.value.connected = false;
      btRemote.value.device_name = '';
      btRemote.value.battery_percentage = null;
    }
    return result.ok;
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
    const appEl = document.getElementById('app');
    if (!appEl) return;
    if (!isKiosk() || scale === 1.0) {
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
  const updateQobuzSettings = makeUpdater(qobuzSettings);
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

  // Also re-applies language: the locale watcher reacts to settingsStore.language.
  // btRemote/irRemote sit outside the settings/bulk payload and are delta-fed
  // (bt_remote_*/ir_remote_status_changed), so refetch them explicitly here.
  async function resync() {
    await Promise.all([
      loadAllSettings(),
      loadBtRemoteStatus(),
      loadIrRemoteStatus(),
    ]);
  }

  return {
    resync,
    // State
    setupCompleted,
    hotspotActive,
    isLoading,
    language,
    volumeLimits,
    volumeStartup,
    volumeSteps,
    dockApps,
    sourceOrder,
    audioPlayback,
    radioSettings,
    qobuzSettings,
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
    updateRadioSettings,
    updateQobuzSettings,
    updateMacRocSettings,
    updateBtRemoteConfig,
    updateBtRemoteStatus,
    loadBtRemoteStatus,
    toggleBtRemote,
    fetchBtRemoteBattery,
    discoverBtRemote,
    unpairBtRemote,
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
