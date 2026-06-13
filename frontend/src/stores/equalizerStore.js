// frontend/src/stores/equalizerStore.js
/**
 * Pinia store for CamillaDSP parametric equalizer
 * Manages equalizer state, filters, and presets
 */
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import { useUnifiedAudioStore } from './unifiedAudioStore';
import { useMultiroomStore } from './multiroomStore';
import { logger } from '@/services/logger';
import { apiCall } from '@/services/apiCall';

// Default 10-band parametric EQ frequencies
const DEFAULT_FREQUENCIES = [31, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000];

// Throttle settings for real-time updates
const THROTTLE_DELAY = 50;
const FINAL_DELAY = 200;

// Uniform per-target EQ API tokens (mirror backend api/equalizer.py::_resolve_target):
//   'local'      → the local DAC (addressable without a registry entry)
//   '<mac>'      → a remote client
//   'zone:<id>'  → a zone (EQ derived from its members)
const LOCAL_TARGET = 'local';
const ZONE_PREFIX = 'zone:';

export const useEqualizerStore = defineStore('equalizer', () => {
  // === STATE ===
  const filters = ref([]);
  const builtinPresets = ref([]); // Array of { id, gains } objects
  const customGains = ref([0, 0, 0, 0, 0, 0, 0, 0, 0, 0]); // Saved custom EQ gains
  const activePreset = ref('flat'); // Preset ID ('flat' default, 'custom' or builtin ID)
  const state = ref('disconnected'); // disconnected, inactive, running, paused
  const isUpdating = ref(false);
  const filtersLoaded = ref(false);

  // Equalizer effects enabled state (persisted in settings)
  // Note: Volume always works via CamillaDSP, this controls EQ/compressor/loudness
  const isEqualizerEffectsEnabled = ref(true);
  const isTogglingEnabled = ref(false);

  // Audio levels (for meters)
  const outputPeak = ref([-80, -80]);

  // Advanced equalizer settings
  const compressor = ref({
    enabled: false,
    threshold: -20,
    ratio: 4,
    attack: 10,
    release: 100,
    makeup_gain: 0
  });

  const loudness = ref({
    enabled: false,
    low_boost: 5,
    high_boost: 5
  });

  const mono = ref(false);

  // Multi-client equalizer support
  // selectedTarget is the MAC address of the target client (e.g., "dc:a6:32:7e:d3:43")
  // Initialized to null - will be auto-selected to local client when registry loads
  const selectedTarget = ref(null);

  // Client registry store - single source of truth for clients and zones
  const registryStore = useMultiroomStore();
  const audioStore = useUnifiedAudioStore();

  // Available equalizer targets - computed from multiroomStore (single source of truth)
  const availableTargets = computed(() => {
    return registryStore.clientList.map(client => ({
      id: client.mac_id,
      name: client.name,
      host: client.host,
      ip: client.ip,
      online: client.online,
      is_local: client.is_local
    }));
  });

  // Linked clients - delegates to multiroomStore.zoneList
  // Structure: [{ id: 'group_1', client_ids: ['dc:a6:32:xx:xx:xx', 'dc:a6:32:yy:yy:yy'], name: 'Zone 1' }]
  const linkedGroups = computed(() => registryStore.zoneList);

  // Client types - builds from multiroomStore.clients
  // Structure: { clientId: { speaker_type: 'satellite'|'bookshelf'|'tower'|'subwoofer', crossover_frequency: number|null } }
  const clientTypes = computed(() => {
    const types = {};
    for (const client of registryStore.clientList) {
      if (client.mac_id) {
        types[client.mac_id] = {
          speaker_type: client.speaker_type || 'bookshelf',
          crossover_frequency: client.crossover_frequency ?? null
        };
      }
    }
    return types;
  });

  // Zone crossover settings - { zoneId: { frequency: 80, enabled: true, has_subwoofer: false } }
  const zoneCrossover = ref({});

  // AbortController for cancelling ongoing requests
  let loadAbortController = null;

  // Throttling management
  const filterThrottleMap = new Map();

  // === COMPUTED ===
  const isConnected = computed(() => state.value !== 'disconnected');

  // Custom mode: active when preset is 'custom' or no preset selected
  const isCustomMode = computed(() => {
    return activePreset.value === 'custom' || !activePreset.value;
  });

  // Edited state: true when gains have been modified from the loaded preset
  const isPresetEdited = ref(false);
  // Snapshot of the gains when a preset was loaded (for edit detection)
  const originalPresetGains = ref(null);

  // Format frequency for display
  const formatFrequency = (freq) => {
    if (freq >= 1000) {
      return `${(freq / 1000).toFixed(freq % 1000 === 0 ? 0 : 1)}k`;
    }
    return freq.toString();
  };

  // === INITIALIZATION ===
  function initializeFilters() {
    // Initialize with default 10-band EQ if no filters loaded
    if (filters.value.length === 0) {
      filters.value = DEFAULT_FREQUENCIES.map((freq, index) => ({
        id: `eq_band_${index.toString().padStart(2, '0')}`,
        freq,
        gain: 0,
        q: 1.41,
        type: 'Peaking',
        enabled: true,
        displayName: formatFrequency(freq)
      }));
    }
  }

  // === API HELPERS ===
  /**
   * Resolve the uniform per-target token for the EQ API.
   * @returns {string} 'local' | '<mac>' | 'zone:<id>'
   */
  function targetRef() {
    // A zone member resolves to its zone (multiroom-gated inside getSelectedZoneId).
    const zoneId = getSelectedZoneId();
    if (zoneId) return `${ZONE_PREFIX}${zoneId}`;
    // The local client has no MAC to address when multiroom is off → the sentinel.
    const target = selectedTarget.value;
    if (!target || isLocalClient(target)) return LOCAL_TARGET;
    // A standalone remote client.
    return target;
  }

  /** Base path for every per-target EQ read/write: /api/equalizer/target/{target}. */
  function targetBase() {
    return `/api/equalizer/target/${targetRef()}`;
  }

  /**
   * Get the zone ID if the selected target is part of a zone.
   * @returns {string|null} Zone ID or null if standalone client
   */
  function getSelectedZoneId() {
    // Zone routing only applies when multiroom is enabled

    if (!audioStore.systemState.multiroom_enabled) return null;

    const zone = registryStore.getZoneForClient(selectedTarget.value);
    return zone ? zone.id : null;
  }

  // === API CALLS ===
  /**
   * Fetch the complete EQ record for the current target (one GET, one record).
   * Returns null when cancelled / failed.
   */
  async function fetchTargetRecord(signal = null) {
    const result = await apiCall.get(targetBase(), {
      category: 'store',
      message: 'Error fetching equalizer record',
      signal,
    });
    return result.ok ? result.data : null;
  }

  async function fetchPresets() {
    // The builtin preset catalog (labels + gains) is global, not per-target.
    // Per-target custom gains / active preset come from the target record itself.
    const result = await apiCall.get('/api/equalizer/presets', {
      category: 'store',
      message: 'Error fetching equalizer presets',
    });
    if (!result.ok) return [];
    builtinPresets.value = result.data.presets || [];
    return builtinPresets.value;
  }

  async function sendFilterUpdate(filterId, filterData) {
    const result = await apiCall.put(`${targetBase()}/filter/${filterId}`, filterData, {
      category: 'store',
      message: 'Error updating filter',
      checkStatus: true,
    });
    return result.ok;
  }

  // Note: fetchLinkedGroups, fetchClientTypes, and fetchAvailableTargets removed
  // linkedGroups and clientTypes now delegate to multiroomStore

  async function fetchEnabledState() {
    // The enabled flag travels in the one per-target record.
    const record = await fetchTargetRecord();
    return record ? (record.enabled ?? true) : true;
  }

  async function setEnabledState(enabled) {
    const result = await apiCall.put(`${targetBase()}/enabled`, { enabled }, {
      category: 'store',
      message: 'Error setting equalizer enabled state',
      checkStatus: true,
    });
    return result.ok;
  }

  // Get clients linked to a specific client (including itself)
  function getLinkedClientIds(clientId) {
    for (const group of linkedGroups.value) {
      if (group.client_ids && group.client_ids.includes(clientId)) {
        return group.client_ids;
      }
    }
    return [clientId]; // Not linked, return just itself
  }

  // Get zone group for a client ID
  function getZoneGroup(clientId) {
    for (const group of linkedGroups.value) {
      if (group.client_ids && group.client_ids.includes(clientId)) {
        return group;
      }
    }
    return null;
  }

  /**
   * Check if a client is the local client using the registry's is_local property.
   * @param {string} clientId - Client identifier (MAC address)
   * @returns {boolean} True if this is the local client
   */
  function isLocalClient(clientId) {
    const client = registryStore.clientList.find(c => c.mac_id === clientId);
    return client?.is_local ?? false;
  }

  // === CLIENT EQUALIZER VOLUMES ===

  /**
   * Convert MAC address to URL format (remove colons).
   * Example: "dc:a6:32:7e:d3:43" -> "dca6327ed343"
   * @param {string} macId - MAC address with colons
   * @returns {string} MAC address without colons for URL path
   */
  function macToUrlFormat(macId) {
    return macId.replace(/:/g, '');
  }

  /**
   * Update equalizer volume for a client via API.
   * Uses MAC-based endpoint (all clients are identified by mac_id).
   * Each client's volume is independent - changing one doesn't affect others.
   * @param {string} clientId - Client identifier (MAC address)
   * @param {number} volumeDb - Volume in dB (-80 to 0)
   * @returns {Promise<boolean>} Success status
   */
  async function updateClientEqualizerVolume(clientId, volumeDb) {
    // Skip remote clients when multiroom is disabled
    if (!isLocalClient(clientId) && !audioStore.systemState.multiroom_enabled) {
      logger.warn('store', `Skipping volume update for ${clientId} - multiroom disabled`);
      return false;
    }

    // All clients use MAC-based endpoint: PATCH /api/volume/client/mac/{mac_url}
    const result = await apiCall.patch(
      `/api/volume/client/mac/${macToUrlFormat(clientId)}`,
      { volume_db: volumeDb },
      {
        category: 'store',
        message: `Error updating equalizer volume for ${clientId}`,
      },
    );
    return result.ok;
  }

  /**
   * Apply volume delta to entire zone atomically.
   *
   * Eliminates race condition:
   * - Old: 3 parallel requests → 3 stale broadcasts → slider flicker
   * - New: 1 request → parallel backend updates → 1 correct broadcast → smooth slider
   *
   * @param {string} zoneId - Zone identifier (UUID)
   * @param {number} deltaDb - Volume change in dB
   * @returns {Promise<object>} Response with new zone average
   */
  async function applyZoneDelta(zoneId, deltaDb) {
    // Check multiroom enabled
    if (!audioStore.systemState.multiroom_enabled) {
      logger.warn('store', 'Skipping zone delta - multiroom disabled');
      return { status: 'error', message: 'Multiroom disabled' };
    }

    // Call atomic zone delta endpoint: PATCH /api/volume/zone/{zone_id}
    const result = await apiCall.patch(`/api/volume/zone/${zoneId}`, { delta_db: deltaDb }, {
      category: 'store',
      message: `Error applying zone delta for ${zoneId}`,
      rethrow: true,
    });
    // Response includes: { status, zone_id, new_average_db, delta_db, applied_to, offline_clients }
    return result.data;
  }

  /**
   * Get equalizer volume for a client from unified volume state
   * @param {string} clientId - Client identifier (MAC address)
   * @returns {number} Volume in dB, defaults to -30 if not found
   */
  function getClientEqualizerVolume(clientId) {

    return audioStore.volumeState.clients[clientId]?.volume_db ?? -30;
  }

  /**
   * Get equalizer mute for a client from unified volume state
   * @param {string} clientId - Client identifier (MAC address)
   * @returns {boolean} Mute state, defaults to false if not found
   */
  function getClientEqualizerMute(clientId) {

    return audioStore.volumeState.clients[clientId]?.mute ?? false;
  }

  /**
   * Update mute for a specific client.
   * By default, mutes only the specified client. Use { propagate: true } to
   * also mute/unmute all zone members if the client is part of a zone.
   *
   * @param {string} clientId - Client identifier (MAC address)
   * @param {boolean} muted - Mute state
   * @param {Object} options - Optional settings
   * @param {boolean} options.propagate - If true, propagate to all zone members (default: false)
   * @returns {Promise<boolean>} Success status
   */
  async function updateClientEqualizerMute(clientId, muted, options = {}) {
    const { propagate = false } = options;

    // Skip remote clients when multiroom is disabled
    if (!isLocalClient(clientId) && !audioStore.systemState.multiroom_enabled) {
      logger.warn('store', `Skipping mute update for ${clientId} - multiroom disabled`);
      return false;
    }

    // All clients use MAC-based endpoint: PATCH /api/volume/client/mac/{mac_url}/mute
    const primary = await apiCall.patch(
      `/api/volume/client/mac/${macToUrlFormat(clientId)}/mute`,
      { mute: muted },
      {
        category: 'store',
        message: `Error updating mute for ${clientId}`,
      },
    );
    if (!primary.ok) return false;

    // If propagate requested and client is part of a zone, update all online zone members
    if (propagate) {
      const linkedIds = registryStore.getLinkedClientIds(clientId);
      if (linkedIds.length > 1) {
        // Only propagate to online clients
        const otherClients = linkedIds.filter(id =>
          id !== clientId && registryStore.isClientOnline(id)
        );
        const promises = otherClients.map(targetId =>
          apiCall.patch(
            `/api/volume/client/mac/${macToUrlFormat(targetId)}/mute`,
            { mute: muted },
            {
              category: 'store',
              message: `Error propagating mute to ${targetId}`,
            },
          ),
        );
        await Promise.all(promises);
      }
    }

    return true;
  }

  // === ACTIONS ===
  async function loadStatus() {
    // Cancel previous request if it exists
    if (loadAbortController) {
      loadAbortController.abort();
    }
    loadAbortController = new AbortController();
    const signal = loadAbortController.signal;

    filtersLoaded.value = false;

    await apiCall('store', 'Error loading equalizer data', async () => {
      // One GET returns the complete record (state, filters, compressor, loudness,
      // mono, active_preset, enabled, custom_gains) for whatever the target is —
      // local DAC, remote client, or zone. No cross-source reconciliation.
      const [record] = await Promise.all([
        fetchTargetRecord(signal),
        fetchPresets(),  // builtin catalog only (labels + gains)
      ]);

      // Cancelled or failed (a newer loadStatus aborted this one).
      if (record === null) return;

      state.value = record.state || 'disconnected';
      isEqualizerEffectsEnabled.value = record.enabled ?? true;

      // Filters (already in freq/type wire shape from the API).
      if (Array.isArray(record.filters) && record.filters.length > 0) {
        filters.value = record.filters.map(f => ({
          ...f,
          displayName: formatFrequency(f.freq)
        }));
      } else {
        initializeFilters();
      }

      // Advanced settings (preserve defaults for any missing field).
      if (record.compressor) {
        compressor.value = { ...compressor.value, ...record.compressor };
      }
      if (record.loudness) {
        loudness.value = { ...loudness.value, ...record.loudness };
      }
      if (record.mono !== undefined) {
        mono.value = record.mono;
      }
      if (Array.isArray(record.custom_gains)) {
        customGains.value = record.custom_gains;
      }
      activePreset.value = record.active_preset || 'flat';

      // Volume data comes from unifiedAudioStore.volumeState via WebSocket
      // No need to update local cache here

      filtersLoaded.value = true;

      // Snapshot current preset gains for edit detection
      isPresetEdited.value = false;
      _snapshotPresetGains(activePreset.value);
    });
    loadAbortController = null;
  }

  function updateFilterValue(filterId, field, value) {
    const filter = filters.value.find(f => f.id === filterId);
    if (filter) {
      filter[field] = value;
      if (field === 'freq') {
        filter.displayName = formatFrequency(value);
      }
    }
  }

  function handleFilterThrottled(filterId, filterData) {
    const now = Date.now();
    let throttleState = filterThrottleMap.get(filterId) || {};

    if (throttleState.throttleTimeout) clearTimeout(throttleState.throttleTimeout);
    if (throttleState.finalTimeout) clearTimeout(throttleState.finalTimeout);

    if (!throttleState.lastRequestTime || (now - throttleState.lastRequestTime) >= THROTTLE_DELAY) {
      sendFilterUpdate(filterId, filterData);
      throttleState.lastRequestTime = now;
    } else {
      throttleState.throttleTimeout = setTimeout(() => {
        sendFilterUpdate(filterId, filterData);
        throttleState.lastRequestTime = Date.now();
      }, THROTTLE_DELAY - (now - throttleState.lastRequestTime));
    }

    throttleState.finalTimeout = setTimeout(() => {
      sendFilterUpdate(filterId, filterData);
      // Entry is cleaned up by clearThrottleForFilter() on drag-end, not here.
      // Removing it here would open a window for WebSocket echo during drag pauses.
    }, FINAL_DELAY);

    filterThrottleMap.set(filterId, throttleState);
  }

  function clearThrottleForFilter(filterId) {
    const throttleState = filterThrottleMap.get(filterId);
    if (throttleState) {
      if (throttleState.throttleTimeout) clearTimeout(throttleState.throttleTimeout);
      if (throttleState.finalTimeout) clearTimeout(throttleState.finalTimeout);

      // Delay removal from throttle map to allow stale WebSocket events to be ignored
      // This prevents race conditions where old events arrive after finalization
      setTimeout(() => {
        filterThrottleMap.delete(filterId);
      }, 300);
    }
  }

  function clearAllThrottles() {
    filterThrottleMap.forEach(throttleState => {
      if (throttleState.throttleTimeout) clearTimeout(throttleState.throttleTimeout);
      if (throttleState.finalTimeout) clearTimeout(throttleState.finalTimeout);
    });
    filterThrottleMap.clear();
  }

  async function updateFilter(filterId, field, value) {
    updateFilterValue(filterId, field, value);

    // Track edit state: mark as edited when any filter parameter changes from loaded preset
    if (!isPresetEdited.value && activePreset.value) {
      if (field === 'gain' && originalPresetGains.value) {
        const currentGains = filters.value.map(f => f.gain);
        const edited = currentGains.some((g, i) => g !== originalPresetGains.value[i]);
        if (edited) {
          isPresetEdited.value = true;
        }
      } else if (field !== 'gain') {
        // Freq, Q, or type changes always count as edits
        isPresetEdited.value = true;
      }
    }

    const filter = filters.value.find(f => f.id === filterId);
    if (filter) {
      handleFilterThrottled(filterId, {
        freq: filter.freq,
        gain: filter.gain,
        q: filter.q,
        filter_type: filter.type
      });
    }
  }

  async function finalizeFilterUpdate(filterId) {
    const filter = filters.value.find(f => f.id === filterId);
    if (filter) {
      const filterData = {
        freq: filter.freq,
        gain: filter.gain,
        q: filter.q,
        filter_type: filter.type
      };

      // sendFilterUpdate handles zone vs direct mode routing
      await sendFilterUpdate(filterId, filterData);
      clearThrottleForFilter(filterId);
    }
  }

  // === PRESET MANAGEMENT ===
  async function loadPreset(presetId) {
    // One uniform route for every target; the response carries the resolved gains.
    const result = await apiCall.post(`${targetBase()}/preset`, { preset_id: presetId }, {
      category: 'store',
      message: 'Error loading preset',
      checkStatus: true,
    });
    if (result.ok) {
      _applyResponseGains(presetId, result.data.gains);
      return true;
    }
    return false;
  }

  /**
   * Apply gains from API response. Updates customGains if needed before applying.
   */
  function _applyResponseGains(presetId, gains) {
    if (presetId === 'custom' && gains) {
      customGains.value = gains;
    }
    activePreset.value = presetId;
    isPresetEdited.value = false;
    _snapshotPresetGains(presetId);
    _applyPresetGains(presetId);
  }

  /**
   * Save current filter gains as the "custom" preset.
   * Posts to the save-custom endpoint, which persists gains and sets active_preset.
   */
  async function saveCustomPreset() {
    const result = await apiCall.post(`${targetBase()}/save-custom`, null, {
      category: 'store',
      message: 'Error saving custom preset',
      checkStatus: true,
    });
    if (result.ok) {
      activePreset.value = 'custom';
      customGains.value = filters.value.map(f => f.gain);
      isPresetEdited.value = false;
      originalPresetGains.value = [...customGains.value];
      return true;
    }
    return false;
  }

  /**
   * Snapshot the gains for the currently loaded preset (for edit detection).
   * @param {string} presetId - Preset ID
   */
  function _snapshotPresetGains(presetId) {
    if (presetId === 'custom') {
      originalPresetGains.value = [...customGains.value];
    } else {
      const preset = builtinPresets.value.find(p => p.id === presetId);
      if (preset) {
        originalPresetGains.value = [...preset.gains];
      } else {
        originalPresetGains.value = null;
      }
    }
  }

  /**
   * Apply preset gains to local filter values.
   * Used after loading a preset to update the UI immediately.
   */
  function _applyPresetGains(presetId) {
    let gains = null;
    if (presetId === 'custom') {
      gains = customGains.value;
    } else {
      const preset = builtinPresets.value.find(p => p.id === presetId);
      if (preset) {
        gains = preset.gains;
      }
    }

    if (gains && filters.value.length > 0) {
      for (let i = 0; i < gains.length && i < filters.value.length; i++) {
        const filterId = `eq_band_${i.toString().padStart(2, '0')}`;
        const filter = filters.value.find(f => f.id === filterId);
        if (filter) {
          filter.gain = gains[i];
        }
      }
    }
  }

  // === ADVANCED FEATURES ===

  async function updateCompressor(settings) {
    // Optimistic update: apply immediately for responsive UI
    const previous = { ...compressor.value };
    Object.assign(compressor.value, settings);

    const result = await apiCall.put(`${targetBase()}/compressor`, settings, {
      category: 'store',
      message: 'Error updating compressor',
      checkStatus: true,
    });

    if (!result.ok) Object.assign(compressor.value, previous);
    return result.ok;
  }

  async function updateLoudness(settings) {
    // Optimistic update: apply immediately for responsive UI
    const previous = { ...loudness.value };
    Object.assign(loudness.value, settings);

    const result = await apiCall.put(`${targetBase()}/loudness`, settings, {
      category: 'store',
      message: 'Error updating loudness',
      checkStatus: true,
    });

    if (!result.ok) Object.assign(loudness.value, previous);
    return result.ok;
  }

  async function updateMono(enabled) {
    const previous = mono.value;
    mono.value = enabled;

    const result = await apiCall.put(`${targetBase()}/mono`, { enabled }, {
      category: 'store',
      message: 'Error updating mono',
      checkStatus: true,
    });
    if (result.ok) return true;
    mono.value = previous;
    return false;
  }

  // === TARGET MANAGEMENT ===

  async function loadTargets() {
    // Ensure multiroomStore is initialized
    // availableTargets is now a computed property that delegates to multiroomStore
    if (!registryStore.isInitialized) {
      await registryStore.initialize();
    }

    // Auto-select local client if no target selected
    if (!selectedTarget.value && availableTargets.value.length > 0) {
      const localTarget = availableTargets.value.find(t => t.is_local);
      if (localTarget) {
        selectedTarget.value = localTarget.id;
      }
    }
  }

  async function selectTarget(targetId) {
    if (targetId === selectedTarget.value) return;

    // Clear current state
    cleanup();
    selectedTarget.value = targetId;

    // The record is the source of truth: one GET reflects the selected target.
    // (Satellites are kept in sync by writes + reconnect re-push, not a restore-on-select.)
    await loadStatus();
    await loadEnabledState();
  }

  // === SPEAKER TYPE / CROSSOVER MANAGEMENT ===

  /**
   * Get the speaker type for a client
   * @param {string} clientId - Client ID (mac_id)
   * @returns {string} Speaker type: 'satellite', 'bookshelf', 'tower', or 'subwoofer'
   */
  function getClientSpeakerType(clientId) {
    const clientData = clientTypes.value[clientId];
    if (!clientData) return 'bookshelf';
    return clientData.speaker_type || 'bookshelf';
  }

  /**
   * Update crossover frequency for a zone
   * @param {string} zoneId - Zone ID
   * @param {number} frequency - Crossover frequency in Hz (40-200)
   * @returns {Promise<boolean>} Success status
   */
  async function setZoneCrossoverFrequency(zoneId, frequency) {
    const result = await apiCall.put(`/api/equalizer/links/${zoneId}/crossover`, { frequency }, {
      category: 'store',
      message: 'Error setting zone crossover',
      checkStatus: true,
    });
    if (result.ok) {
      zoneCrossover.value[zoneId] = {
        ...zoneCrossover.value[zoneId],
        frequency: result.data.frequency,
        enabled: result.data.enabled,
        has_subwoofer: result.data.has_subwoofer,
      };
      return true;
    }
    return false;
  }

  /**
   * Handle zone crossover changed events.
   * Schema in @/schemas/ws.js → 'multiroom.crossover_changed'.
   * @param {{zone_id: string, crossover_enabled: boolean, crossover_frequency: number}} payload
   */
  function handleZoneCrossoverChanged(payload) {
    if (!payload.zone_id) return;
    zoneCrossover.value[payload.zone_id] = {
      frequency: payload.crossover_frequency,
      enabled: payload.crossover_enabled,
      has_subwoofer: false,
    };
  }

  // === WEBSOCKET HANDLERS ===

  /**
   * Handle equalizer changed events from multiroom category.
   * Updates local equalizer state when the target matches selectedTarget.
   * Consumes the validated payload from parsedOn (schema: multiroom.equalizer_changed).
   * @param {{ target_type: "zone"|"client", target_id: string,
   *   equalizer_settings: { filters?, compressor?, loudness?, mono?, active_preset?,
   *   enabled?, custom_gains? } }} payload
   */
  function handleEqualizerChanged(payload) {
    const { target_type, target_id, equalizer_settings } = payload;
    if (!equalizer_settings) return;

    // ALWAYS update activePreset for zone events if we have a client in that zone
    // This must happen BEFORE the relevance check for filter updates, because
    // the relevance check may fail while the preset change is still valid
    if (target_type === 'zone' && equalizer_settings.active_preset !== undefined) {
      const zone = registryStore.getZoneForClient(selectedTarget.value);
      if (zone && zone.id === target_id && equalizer_settings.active_preset !== activePreset.value) {
        activePreset.value = equalizer_settings.active_preset;
        isPresetEdited.value = false;
        _snapshotPresetGains(equalizer_settings.active_preset);
      }
    }

    // Check if this equalizer change applies to the currently selected target
    let isRelevant = false;

    if (target_type === 'client') {
      // Direct client match
      isRelevant = target_id === selectedTarget.value;
    } else if (target_type === 'zone') {
      // Zone match: check if selectedTarget is in this zone
      const zone = registryStore.getZoneForClient(selectedTarget.value);
      isRelevant = zone && zone.id === target_id;
    }

    if (!isRelevant) return;

    // Update local equalizer state from received settings
    if (equalizer_settings.filters && Array.isArray(equalizer_settings.filters)) {
      for (const filterData of equalizer_settings.filters) {
        // Skip if this specific filter is being actively edited (avoids echo conflicts)
        if (filterThrottleMap.has(filterData.id)) continue;

        const filter = filters.value.find(f => f.id === filterData.id);
        if (filter) {
          // Only update if values actually changed (avoids unnecessary reactivity triggers)
          if (filterData.freq !== undefined && filter.freq !== filterData.freq) {
            filter.freq = filterData.freq;
            filter.displayName = formatFrequency(filterData.freq);
          }
          if (filterData.gain !== undefined && filter.gain !== filterData.gain) filter.gain = filterData.gain;
          if (filterData.q !== undefined && filter.q !== filterData.q) filter.q = filterData.q;
          if (filterData.type !== undefined && filter.type !== filterData.type) filter.type = filterData.type;
        }
      }
    }

    if (equalizer_settings.compressor) {
      Object.assign(compressor.value, equalizer_settings.compressor);
    }

    if (equalizer_settings.loudness) {
      Object.assign(loudness.value, equalizer_settings.loudness);
    }

    if (equalizer_settings.mono !== undefined) {
      mono.value = equalizer_settings.mono;
    }

    // Update active preset if it actually changed (avoid resetting edit state on echo)
    if (equalizer_settings.active_preset !== undefined && equalizer_settings.active_preset !== activePreset.value) {
      activePreset.value = equalizer_settings.active_preset;
      isPresetEdited.value = false;
      _snapshotPresetGains(equalizer_settings.active_preset);
    }
  }

  function handleFilterChanged(event) {
    if (!event.data) return;
    const { id, freq, gain, q, type } = event.data;
    const filter = filters.value.find(f => f.id === id);

    // Skip if this specific filter is being actively edited (avoids echo conflicts)
    // This is more precise than checking filterThrottleMap.size === 0
    if (filter && !filterThrottleMap.has(id)) {
      // Only update if values actually changed (avoids unnecessary reactivity triggers)
      if (freq !== undefined && filter.freq !== freq) {
        filter.freq = freq;
        filter.displayName = formatFrequency(freq);
      }
      if (gain !== undefined && filter.gain !== gain) filter.gain = gain;
      if (q !== undefined && filter.q !== q) filter.q = q;
      if (type !== undefined && filter.type !== type) filter.type = type;
    }
  }

  function handleStateChanged(payload) {
    state.value = payload.state;
  }

  /**
   * Handle pushed level samples while the monitor is armed.
   * Schema in @/schemas/ws.js → 'equalizer.levels'.
   * @param {{available: boolean, output_peak: number[]}} payload
   */
  function handleLevelsChanged(payload) {
    outputPeak.value = payload.available ? payload.output_peak : [-80, -80];
  }

  /**
   * Keepalive for the backend levels monitor (WS push, ~4 Hz). Open meter
   * views re-call this every few seconds; the backend stops sampling ~15 s
   * after the last call.
   * @param {string[]} clientIds - clients to aggregate (empty = local DAC)
   */
  async function keepLevelsMonitorAlive(clientIds = []) {
    await apiCall.post('/api/equalizer/levels/monitor', { client_ids: clientIds }, {
      category: 'equalizer',
      message: 'Error arming levels monitor',
      logLevel: 'debug',
    });
  }

  function handleCompressorChanged(payload) {
    Object.assign(compressor.value, payload);
  }

  function handleLoudnessChanged(payload) {
    Object.assign(loudness.value, payload);
  }

  function handleMonoChanged(event) {
    if (event.data?.enabled !== undefined) {
      mono.value = event.data.enabled;
    }
  }

  // Note: handleClientNameChanged removed - availableTargets is now a computed
  // property that automatically updates when multiroomStore changes

  // === CLEANUP ===
  function cleanup() {
    // Cancel pending requests
    if (loadAbortController) {
      loadAbortController.abort();
      loadAbortController = null;
    }
    clearAllThrottles();
    filtersLoaded.value = false;

    // Reset equalizer state to defaults to prevent showing stale data while loading
    // This ensures users see flat/disabled state instead of previous zone's settings
    for (const filter of filters.value) {
      filter.gain = 0;
    }
    loudness.value = { enabled: false, low_boost: 5, high_boost: 5 };
    compressor.value = { enabled: false, threshold: -20, ratio: 4, attack: 10, release: 100, makeup_gain: 0 };
    mono.value = false;
    activePreset.value = 'flat';
    isPresetEdited.value = false;
    originalPresetGains.value = null;
  }

  // === EQUALIZER EFFECTS ENABLE/DISABLE ===
  async function loadEnabledState() {
    isEqualizerEffectsEnabled.value = await fetchEnabledState();
    return isEqualizerEffectsEnabled.value;
  }

  async function toggleEqualizerEffectsEnabled(enabled) {
    if (isTogglingEnabled.value) return false;

    const previousState = isEqualizerEffectsEnabled.value;
    isTogglingEnabled.value = true;
    isEqualizerEffectsEnabled.value = enabled;

    // One uniform write for every target (local / remote / zone).
    const success = await setEnabledState(enabled);

    if (success) {
      if (enabled) {
        await loadStatus();
      } else {
        cleanup();
      }
    } else {
      isEqualizerEffectsEnabled.value = previousState;
    }

    isTogglingEnabled.value = false;
    return success;
  }

  function handleEnabledChanged(event) {
    if (event.data && event.data.enabled !== undefined) {
      isEqualizerEffectsEnabled.value = event.data.enabled;
    }
  }

  function handleZoneEnabledChanged(event) {
    if (!event.data) return;
    const { zone_id, enabled } = event.data;
    if (enabled === undefined) return;

    // Only update if our selected target is in the affected zone
    const zone = registryStore.getZoneForClient(selectedTarget.value);
    if (zone && zone.id === zone_id) {
      isEqualizerEffectsEnabled.value = enabled;
    }
  }

  async function resync() {
    return loadStatus();
  }

  return {
    resync,
    // State
    filters,
    activePreset,
    isUpdating,
    filtersLoaded,
    outputPeak,

    // Equalizer Effects Enabled State
    isEqualizerEffectsEnabled,
    isTogglingEnabled,

    // Advanced Equalizer State
    compressor,
    loudness,
    mono,

    // Multi-client support
    selectedTarget,
    availableTargets,
    linkedGroups,

    // Computed
    isConnected,

    // Actions
    initializeFilters,
    loadStatus,
    updateFilter,
    finalizeFilterUpdate,
    cleanup,

    // Equalizer Effects Enable/Disable
    loadEnabledState,
    toggleEqualizerEffectsEnabled,

    // Target Management
    loadTargets,
    selectTarget,

    // Linked Clients Management
    getLinkedClientIds,
    getZoneGroup,

    // Speaker Type / Crossover Management
    getClientSpeakerType,
    setZoneCrossoverFrequency,
    handleZoneCrossoverChanged,

    // Preset Management
    builtinPresets,
    isCustomMode,
    isPresetEdited,
    loadPreset,
    saveCustomPreset,

    // Advanced Features
    updateCompressor,
    updateLoudness,
    updateMono,

    // Client equalizer volume/mute (reads from unified store)
    updateClientEqualizerVolume,
    applyZoneDelta,  // Atomic zone volume update
    getClientEqualizerVolume,
    getClientEqualizerMute,
    updateClientEqualizerMute,  // Use { propagate: true } for zone propagation

    // Levels monitor
    keepLevelsMonitorAlive,

    // WebSocket Handlers
    handleEqualizerChanged,
    handleFilterChanged,
    handleStateChanged,
    handleLevelsChanged,
    handleCompressorChanged,
    handleLoudnessChanged,
    handleMonoChanged,
    handleEnabledChanged,
    handleZoneEnabledChanged
  };
});
