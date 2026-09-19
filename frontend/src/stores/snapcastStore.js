// frontend/src/stores/snapcastStore.js
/**
 * Snapcast store for Snapcast server configuration.
 *
 * Client data is derived from multiroomStore (single source of truth).
 * This store only manages Snapcast-specific server settings.
 */
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import { useMultiroomStore } from './multiroomStore';
import { useUnifiedAudioStore } from './unifiedAudioStore';
import { logger } from '@/services/logger';
import { apiCall } from '@/services/apiCall';
import { SnapcastCapabilitiesSchema, SnapcastServerConfigSchema, validateSchema } from '@/schemas/api';
import { dbToPercent } from '@/constants/volumeConversion';

const DISPLAY_CACHE_KEY = 'multiroom_display_cache';

export const useSnapcastStore = defineStore('snapcast', () => {
  // === DERIVED STATE FROM MULTIROOM REGISTRY ===
  const registryStore = useMultiroomStore();
  const audioStore = useUnifiedAudioStore();

  // Clients derived from multiroomStore with Snapcast-compatible format
  const clients = computed(() => {
    return registryStore.clientList.map(client => {
      const volumeState = audioStore.volumeState.clients[client.mac_id];
      return {
        // Use mac_id as primary ID (snapcast_id not available in registry)
        id: client.mac_id,
        mac_id: client.mac_id,
        name: client.name,
        host: client.host,
        ip: client.ip,
        online: client.online,
        is_local: client.is_local,
        volume_control: client.volume_control,
        eq_independent: client.eq_independent ?? false,
        delay_ms: client.delay_ms ?? 0,
        gain_db: client.gain_db ?? 0,
        // Convert dB to percentage for UI
        volume: dbToPercent(volumeState?.volume_db ?? -60),
        muted: volumeState?.mute ?? false,
        last_seen_age: 0 // Not tracked here, use registry if needed
      };
    });
  });

  let serverConfigAbortController = null;
  const isLoading = computed(() => !registryStore.isInitialized);

  // Placeholder shape only — template v-models need an object before the
  // first fetch; real values come from GET /server-config (backend is the
  // single source for config, codec list, and presets). Keys must match what
  // fetchServerConfig() returns, or a slider binds undefined until it lands.
  const PLACEHOLDER_SERVER_CONFIG = {
    buffer_ms: 1000,
    codec: 'flac',
    chunk_ms: 20,
    sampleformat: '48000:32:2',
    snapclient_buffer_time: 80
  };
  const serverConfig = ref({ ...PLACEHOLDER_SERVER_CONFIG });
  const originalServerConfig = ref({ ...PLACEHOLDER_SERVER_CONFIG });
  const isApplyingServerConfig = ref(false);
  // The last write, kept so a discard landing mid-flight can wait for its
  // outcome rather than pull the buffer out from under the request. Settled,
  // awaiting it costs one microtask.
  let lastApply = Promise.resolve();

  // Backend-declared capabilities (codec whitelist + quality presets),
  // populated alongside the server config fetch.
  const capabilities = ref({ codecs: [], presets: [] });

  // Automatic analysis. `stage` and `result` arrive as WS deltas, which are
  // never replayed — hence resync() below and this store's membership of
  // App.vue's deltaStores. Without it a tab backgrounded across a run comes
  // back showing an idle button over a finished analysis.
  const calibration = ref({
    running: false, stage: null, result: null, error: null,
    // The backend declares how long it expects to take; the UI animates against
    // it and never completes the bar on its own. `startedAt` is local because
    // the only clock the bar can trust is the one it is drawn on.
    expectedSeconds: 0, startedAt: 0,
  });

  // Memorization of display items structure (for zone-aware skeletons)
  // Each item: { type: 'zone' | 'client' }
  const lastKnownDisplayItems = ref([
    { type: 'client' },
    { type: 'client' },
    { type: 'client' }
  ]);

  // === COMPUTED ===
  const hasServerConfigChanges = computed(() => {
    // Key by key, not JSON.stringify: that compares insertion order too. The
    // placeholder lists its keys in one order and the API answers in another,
    // so a buffer identical in every field read as a pending change and lit
    // the Apply button over nothing to apply.
    const edited = serverConfig.value;
    const applied = originalServerConfig.value;
    const keys = new Set([...Object.keys(edited), ...Object.keys(applied)]);
    return [...keys].some((key) => edited[key] !== applied[key]);
  });

  // === DISPLAY CACHE MANAGEMENT ===
  function loadDisplayCache() {
    try {
      const cached = localStorage.getItem(DISPLAY_CACHE_KEY);
      if (!cached) return null;
      return JSON.parse(cached);
    } catch (error) {
      logger.warn('store', 'Error loading display cache', error);
      return null;
    }
  }

  function saveDisplayCache(displayItems) {
    try {
      const items = displayItems.map(item => ({
        type: item.isZone ? 'zone' : 'client',
        mac_id: item.mac_id || null
      }));
      localStorage.setItem(DISPLAY_CACHE_KEY, JSON.stringify(items));
      lastKnownDisplayItems.value = items;
    } catch (error) {
      logger.error('store', 'Error saving display cache', error);
    }
  }

  function preloadDisplayCache() {
    const cache = loadDisplayCache();
    if (cache && cache.length > 0) {
      lastKnownDisplayItems.value = cache;
    }
  }

  // === API CALLS ===

  async function fetchServerConfig(signal = null) {
    const result = await apiCall.get('/api/routing/snapcast/server-config', {
      category: 'store',
      message: 'Error fetching server config',
      signal,
    });
    if (!result.ok) return null;

    // Capabilities are static and served even when snapserver is down.
    const caps = validateSchema(SnapcastCapabilitiesSchema, result.data.capabilities, 'snapcast.capabilities');
    if (caps.success) {
      capabilities.value = caps.data;
    }

    if (!result.data?.config) return null;

    const parsed = validateSchema(SnapcastServerConfigSchema, result.data.config, 'snapcast.server-config');
    if (!parsed.success) return null;

    // No translation: the GET returns the shape applyServerConfig sends back.
    return parsed.data;
  }

  // === ACTIONS - CLIENTS ===

  /**
   * Ensure the client registry is initialized (delegates to multiroomStore).
   */
  async function loadClients() {
    if (!registryStore.isInitialized) {
      await registryStore.resync();
    }
  }

  // === ACTIONS - SERVER CONFIG ===
  async function loadServerConfig() {
    if (serverConfigAbortController) {
      serverConfigAbortController.abort();
    }
    serverConfigAbortController = new AbortController();
    const signal = serverConfigAbortController.signal;

    await apiCall('store', 'Error loading server config', async () => {
      const config = await fetchServerConfig(signal);
      if (!config) return;

      // The applied reference always refreshes; the edit buffer only when the
      // user has nothing staged. This reload runs whenever the multiroom panel
      // refetches — which includes right after an apply, because snapserver
      // restarts — and overwriting unconditionally made a staged proposal, and
      // its Apply button, vanish on their own.
      const pending = hasServerConfigChanges.value;
      originalServerConfig.value = { ...config };
      if (!pending) {
        serverConfig.value = config;
      }
    });
    serverConfigAbortController = null;
  }

  /**
   * Drop what was staged and never applied. The edit buffer is store state and
   * outlives the panel, so a proposal left alone came back on the sliders — and
   * brought its Apply button with it — the next time the panel opened.
   */
  async function discardServerConfigChanges() {
    // Mid-apply the buffer IS what the server is being handed, and the outcome
    // is what decides which configuration counts as applied: waiting keeps the
    // new one on success and restores the old one on failure. Returning early
    // instead left a refused apply staged for good — nothing runs the discard
    // a second time, and loadServerConfig deliberately spares a staged buffer.
    await lastApply;
    serverConfig.value = { ...originalServerConfig.value };
  }

  async function applyServerConfig() {
    if (!hasServerConfigChanges.value || isApplyingServerConfig.value) return false;

    lastApply = _writeServerConfig();
    return lastApply;
  }

  async function _writeServerConfig() {
    isApplyingServerConfig.value = true;
    const result = await apiCall.put('/api/routing/snapcast/server-config', {
      config: serverConfig.value,
    }, {
      category: 'store',
      message: 'Error applying multiroom server config',
    });
    isApplyingServerConfig.value = false;

    if (result.ok) {
      originalServerConfig.value = { ...serverConfig.value };
      logger.info('store', 'Multiroom server config applied successfully');
      return true;
    }
    return false;
  }

  // === ACTIONS - AUTOMATIC ANALYSIS ===

  /**
   * Start the analysis. The proposal arrives over WS, not in this response:
   * measuring the fleet takes tens of seconds.
   */
  async function startCalibration(quality = 'lossless') {
    if (calibration.value.running) return false;

    calibration.value = {
      running: true, stage: 'probing', result: null, error: null,
      expectedSeconds: 0, startedAt: Date.now(),
    };
    const result = await apiCall.post('/api/routing/snapcast/calibration', { quality }, {
      category: 'store',
      message: 'Error starting multiroom analysis',
    });

    if (!result.ok) {
      calibration.value = {
        ...calibration.value, running: false, stage: null, error: 'start_failed',
      };
      return false;
    }
    return true;
  }

  /** Whole-event handler for the three `routing/calibration_*` deltas. */
  function handleCalibrationEvent(event) {
    const data = event?.data || {};
    if (event?.type === 'calibration_progress') {
      calibration.value = {
        ...calibration.value,
        running: true,
        stage: data.stage,
        error: null,
        expectedSeconds: data.expected_seconds || calibration.value.expectedSeconds,
        // A client that joined mid-run has no start of its own to measure from.
        startedAt: calibration.value.startedAt || Date.now(),
      };
    } else if (event?.type === 'calibration_result') {
      calibration.value = {
        ...calibration.value, running: false, stage: null, result: data, error: null,
      };
    } else if (event?.type === 'calibration_failed') {
      calibration.value = {
        ...calibration.value, running: false, stage: null, result: null,
        error: data.reason || 'probe_failed', detail: data.detail || null,
      };
    }
  }

  async function loadCalibration() {
    const result = await apiCall.get('/api/routing/snapcast/calibration', {
      category: 'store',
      message: 'Error loading multiroom analysis state',
    });
    if (result.ok && result.data?.status === 'success') {
      // A failure is held locally and the backend keeps no record of it, so
      // clearing it here would wipe the message on the next tab refocus and
      // leave an empty Auto tab where an explanation had been.
      const running = Boolean(result.data.running);
      const recovered = result.data.result || null;
      const keepError = !running && !recovered ? calibration.value.error : null;
      // The server reports elapsed, not a start time: turning a timestamp into
      // a bar position would mean trusting this browser's clock against the
      // appliance's, and the two are only as close as whoever set them.
      const elapsed = Number(result.data.elapsed_seconds) || 0;
      calibration.value = {
        ...calibration.value,
        running,
        stage: running ? 'probing' : null,
        result: recovered,
        error: keepError,
        detail: keepError ? calibration.value.detail : null,
        expectedSeconds: Number(result.data.expected_seconds) || calibration.value.expectedSeconds,
        startedAt: running ? Date.now() - elapsed * 1000 : 0,
      };
    }
  }

  /**
   * Load the proposal into the edit buffer. It is NOT written here: the user
   * still presses apply, and the write goes through applyServerConfig like any
   * other change, so snapserver.conf keeps exactly one writer.
   */
  function stageCalibrationResult() {
    const proposed = calibration.value.result?.config;
    if (!proposed) return false;
    applyPreset({ config: proposed });
    return true;
  }

  /** Delta-fed state healer — see App.vue's deltaStores. */
  async function resync() {
    await loadCalibration();
  }

  function selectCodec(codecName) {
    serverConfig.value.codec = codecName;
  }

  function applyPreset(preset) {
    serverConfig.value.buffer_ms = preset.config.buffer_ms;
    serverConfig.value.codec = preset.config.codec;
    serverConfig.value.chunk_ms = preset.config.chunk_ms;
    if (preset.config.snapclient_buffer_time !== undefined) {
      serverConfig.value.snapclient_buffer_time = preset.config.snapclient_buffer_time;
    }
  }

  return {
    // State (clients is computed from multiroomStore, already sorted: local first, then alphabetical)
    clients,
    isLoading,
    serverConfig,
    capabilities,
    calibration,
    isApplyingServerConfig,
    lastKnownDisplayItems,

    // Computed
    hasServerConfigChanges,

    // Actions - Clients
    loadClients,

    // Actions - Display Cache
    preloadDisplayCache,
    saveDisplayCache,

    // Actions - Server Config
    // fetchServerConfig is exported raw (it mutates nothing but `capabilities`)
    // because the lyrics view needs the live buffer_ms without touching the
    // settings page's edit buffer — loadServerConfig() would overwrite it.
    fetchServerConfig,
    loadServerConfig,
    applyServerConfig,
    discardServerConfigChanges,
    selectCodec,
    applyPreset,

    // Actions - Automatic analysis
    startCalibration,
    handleCalibrationEvent,
    loadCalibration,
    stageCalibrationResult,
    resync,
  };
});
