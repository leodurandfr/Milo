// frontend/src/stores/equalizerStore.js
/**
 * Pinia store for CamillaDSP parametric equalizer
 * Manages equalizer state, filters, and presets
 */
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import axios from 'axios';
import { useUnifiedAudioStore } from './unifiedAudioStore';
import { useMultiroomStore } from './multiroomStore';
import { logger } from '@/services/logger';

// Default 10-band parametric EQ frequencies
const DEFAULT_FREQUENCIES = [31, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000];

// Throttle settings for real-time updates
const THROTTLE_DELAY = 50;
const FINAL_DELAY = 200;

export const useEqualizerStore = defineStore('equalizer', () => {
  // === STATE ===
  const filters = ref([]);
  const builtinPresets = ref([]); // Array of { id, gains } objects
  const manualGains = ref([0, 0, 0, 0, 0, 0, 0, 0, 0, 0]); // Saved manual EQ gains
  const activePreset = ref(null); // Preset ID ('manual' or builtin ID)
  const state = ref('disconnected'); // disconnected, inactive, running, paused
  const isLoading = ref(false);
  const isUpdating = ref(false);
  const isResetting = ref(false);
  const isLoadingPreset = ref(false); // Flag to prevent "Manual" flicker during preset load
  const filtersLoaded = ref(false);
  const sampleRate = ref(48000);

  // Equalizer effects enabled state (persisted in settings)
  // Note: Volume always works via CamillaDSP, this controls EQ/compressor/loudness
  const isEqualizerEffectsEnabled = ref(true);
  const isTogglingEnabled = ref(false);

  // Audio levels (for meters)
  const inputPeak = ref([-80, -80]);
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

  // Multi-client equalizer support
  // selectedTarget is the MAC address of the target client (e.g., "dc:a6:32:7e:d3:43")
  // Initialized to null - will be auto-selected to local client when registry loads
  const selectedTarget = ref(null);

  // Client registry store - single source of truth for clients and zones
  const registryStore = useMultiroomStore();

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

  // Default crossover frequencies per speaker type (mirrors backend)
  const DEFAULT_CROSSOVER_FREQUENCIES = {
    satellite: 120,
    bookshelf: 80,
    tower: 50,
    subwoofer: null
  };

  // Zone crossover settings - { zoneId: { frequency: 80, enabled: true, has_subwoofer: false } }
  const zoneCrossover = ref({});

  // Propagation errors - for showing failed client syncs
  // Structure: [{ clientId: 'hostname', setting: 'filter', error: 'Cannot reach client', timestamp: Date }]
  const propagationErrors = ref([]);

  // Auto-clear errors after 10 seconds
  let errorClearTimeout = null;

  // AbortController for cancelling ongoing requests
  let loadAbortController = null;

  // Throttling management
  const filterThrottleMap = new Map();

  // === COMPUTED ===
  const isConnected = computed(() => state.value !== 'disconnected');
  const isRunning = computed(() => state.value === 'running');

  // Manual mode: active when preset is 'manual' or no preset selected
  // Backend handles switching to manual when gains change via WebSocket events
  const isManualMode = computed(() => {
    return activePreset.value === 'manual' || !activePreset.value;
  });

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
  function getApiBase(targetId = selectedTarget.value) {
    // If target is part of a zone, use local API (source of truth for zone equalizer)
    // This ensures zone equalizer works even when some zone members are offline
    const zone = registryStore.getZoneForClient(targetId);
    if (zone) {
      return '/api/equalizer';
    }
    // If targeting a standalone remote client, use proxy endpoint
    if (targetId && !isLocalClient(targetId)) {
      return `/api/equalizer/client/${targetId}`;
    }
    return '/api/equalizer';
  }

  /**
   * Get the zone ID if the selected target is part of a zone.
   * @returns {string|null} Zone ID or null if standalone client
   */
  function getSelectedZoneId() {
    const zone = registryStore.getZoneForClient(selectedTarget.value);
    return zone ? zone.id : null;
  }

  /**
   * Check if the selected target is part of a zone.
   * @returns {boolean} True if in a zone
   */
  function isTargetInZone() {
    return getSelectedZoneId() !== null;
  }

  // === API CALLS ===
  async function fetchStatus(signal = null) {
    try {
      const response = await axios.get(`${getApiBase()}/status`, { signal });
      return response.data;
    } catch (error) {
      if (axios.isCancel(error) || error.name === 'CanceledError') {
        return null;
      }
      logger.error('store', 'Error fetching equalizer status', error);
      return null;
    }
  }

  async function fetchZoneEqualizer(zoneId, signal = null) {
    try {
      const response = await axios.get(`/api/equalizer/zone/${zoneId}`, { signal });
      return response.data;
    } catch (error) {
      if (axios.isCancel(error) || error.name === 'CanceledError') {
        return null;
      }
      logger.error('store', 'Error fetching zone equalizer', error);
      return null;
    }
  }

  async function fetchFilters(signal = null) {
    try {
      const response = await axios.get(`${getApiBase()}/filters`, { signal });
      return response.data.filters || [];
    } catch (error) {
      if (axios.isCancel(error) || error.name === 'CanceledError') {
        return null;
      }
      logger.error('store', 'Error fetching equalizer filters', error);
      return [];
    }
  }

  async function fetchPresets() {
    try {
      // Presets are always fetched from local Milo
      const response = await axios.get('/api/equalizer/presets');
      builtinPresets.value = response.data.presets || [];
      manualGains.value = response.data.manual_gains || [0, 0, 0, 0, 0, 0, 0, 0, 0, 0];
      activePreset.value = response.data.active_preset || 'manual';
      return builtinPresets.value;
    } catch (error) {
      logger.error('store', 'Error fetching equalizer presets', error);
      return [];
    }
  }

  async function sendFilterUpdate(filterId, filterData) {
    try {
      // Check if we should use zone endpoint
      const audioStore = useUnifiedAudioStore();
      const multiroomEnabled = audioStore.systemState.multiroom_enabled;
      const zoneId = multiroomEnabled ? getSelectedZoneId() : null;

      if (zoneId) {
        // Zone: use zone endpoint
        const response = await axios.patch(`/api/equalizer/zone/${zoneId}/filter/${filterId}`, filterData);
        return response.data.status === 'success' || response.data.status === 'partial';
      } else {
        // Direct mode or standalone: use local endpoint
        const response = await axios.put(`${getApiBase()}/filter/${filterId}`, filterData);
        return response.data.status === 'success';
      }
    } catch (error) {
      logger.error('store', 'Error updating filter', error);
      return false;
    }
  }

  async function sendResetFilters() {
    try {
      const response = await axios.post(`${getApiBase()}/reset`);
      return response.data.status === 'success';
    } catch (error) {
      logger.error('store', 'Error resetting filters', error);
      return false;
    }
  }

  // Note: fetchLinkedGroups, fetchClientTypes, and fetchAvailableTargets removed
  // linkedGroups and clientTypes now delegate to multiroomStore

  async function fetchZoneCrossover(zoneId) {
    try {
      const response = await axios.get(`/api/equalizer/links/${zoneId}/crossover`);
      return response.data || { frequency: 80, enabled: false, has_subwoofer: false };
    } catch (error) {
      logger.error('store', 'Error fetching zone crossover', error);
      return { frequency: 80, enabled: false, has_subwoofer: false };
    }
  }

  async function fetchEnabledState() {
    try {
      const response = await axios.get('/api/equalizer/enabled');
      return response.data.enabled ?? true;
    } catch (error) {
      logger.error('store', 'Error fetching equalizer enabled state', error);
      return true;
    }
  }

  async function setEnabledState(enabled) {
    try {
      const response = await axios.put('/api/equalizer/enabled', { enabled });
      return response.data.status === 'success';
    } catch (error) {
      logger.error('store', 'Error setting equalizer enabled state', error);
      return false;
    }
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

  // Check if a client is linked to another client
  function isClientLinked(clientId) {
    return linkedGroups.value.some(
      group => group.client_ids && group.client_ids.includes(clientId)
    );
  }

  // Get zone name for a client or zone ID
  function getZoneName(clientIdOrZoneId) {
    // If it's a zone: prefix, extract client IDs and find the group
    if (typeof clientIdOrZoneId === 'string' && clientIdOrZoneId.startsWith('zone:')) {
      const clientIds = clientIdOrZoneId.replace('zone:', '').split(',');
      for (const group of linkedGroups.value) {
        if (group.client_ids && clientIds.some(id => group.client_ids.includes(id))) {
          return group.name || null;
        }
      }
      return null;
    }

    // Find group containing this client
    for (const group of linkedGroups.value) {
      if (group.client_ids && group.client_ids.includes(clientIdOrZoneId)) {
        return group.name || null;
      }
    }
    return null;
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
   * Check if a string is a MAC address (contains colons).
   * @param {string} id - Client identifier
   * @returns {boolean} True if id looks like a MAC address
   */
  function isMacAddress(id) {
    return id && id.includes(':');
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
    try {
      // Skip remote clients when multiroom is disabled
      if (!isLocalClient(clientId)) {
        const audioStore = useUnifiedAudioStore();
        if (!audioStore.systemState.multiroom_enabled) {
          logger.warn('store', `Skipping volume update for ${clientId} - multiroom disabled`);
          return false;
        }
      }

      // All clients use MAC-based endpoint: PATCH /api/volume/client/mac/{mac_url}
      await axios.patch(`/api/volume/client/mac/${macToUrlFormat(clientId)}`, { volume_db: volumeDb });
      return true;
    } catch (error) {
      logger.error('store', `Error updating equalizer volume for ${clientId}`, error);
      return false;
    }
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
    try {
      // Check multiroom enabled
      const audioStore = useUnifiedAudioStore();
      if (!audioStore.systemState.multiroom_enabled) {
        logger.warn('store', 'Skipping zone delta - multiroom disabled');
        return { status: 'error', message: 'Multiroom disabled' };
      }

      // Call atomic zone delta endpoint: PATCH /api/volume/zone/{zone_id}
      const response = await axios.patch(`/api/volume/zone/${zoneId}`, { delta_db: deltaDb });

      // Response includes: { status, zone_id, new_average_db, delta_db, applied_to, offline_clients }
      return response.data;
    } catch (error) {
      logger.error('store', `Error applying zone delta for ${zoneId}`, error);
      throw error;
    }
  }

  /**
   * Get equalizer volume for a client from unified volume state
   * @param {string} clientId - Client identifier (MAC address)
   * @returns {number} Volume in dB, defaults to -30 if not found
   */
  function getClientEqualizerVolume(clientId) {
    const audioStore = useUnifiedAudioStore();
    return audioStore.volumeState.clients[clientId]?.volume_db ?? -30;
  }

  /**
   * Get equalizer mute for a client from unified volume state
   * @param {string} clientId - Client identifier (MAC address)
   * @returns {boolean} Mute state, defaults to false if not found
   */
  function getClientEqualizerMute(clientId) {
    const audioStore = useUnifiedAudioStore();
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

    try {
      // Skip remote clients when multiroom is disabled
      if (!isLocalClient(clientId)) {
        const audioStore = useUnifiedAudioStore();
        if (!audioStore.systemState.multiroom_enabled) {
          logger.warn('store', `Skipping mute update for ${clientId} - multiroom disabled`);
          return false;
        }
      }

      // All clients use MAC-based endpoint: PATCH /api/volume/client/mac/{mac_url}/mute
      await axios.patch(`/api/volume/client/mac/${macToUrlFormat(clientId)}/mute`, { mute: muted });

      // If propagate requested and client is part of a zone, update all online zone members
      if (propagate) {
        const linkedIds = registryStore.getLinkedClientIds(clientId);
        if (linkedIds.length > 1) {
          // Only propagate to online clients
          const otherClients = linkedIds.filter(id =>
            id !== clientId && registryStore.isClientOnline(id)
          );
          const promises = otherClients.map(async (targetId) => {
            try {
              // All clients use MAC-based endpoint
              await axios.patch(`/api/volume/client/mac/${macToUrlFormat(targetId)}/mute`, { mute: muted });
            } catch (error) {
              logger.error('store', `Error propagating mute to ${targetId}`, error);
            }
          });
          await Promise.all(promises);
        }
      }

      return true;
    } catch (error) {
      logger.error('store', `Error updating mute for ${clientId}`, error);
      return false;
    }
  }

  /**
   * Propagate any equalizer setting to linked clients.
   * Only propagates to available (connected) clients.
   * @param {string} endpoint - API endpoint (e.g., 'mute', 'compressor', 'preset')
   * @param {object} data - Data to propagate
   * @returns {{ success: boolean, errors: Array<{targetId: string, error: string}>, skipped: Array<string> }}
   */
  async function propagateToLinkedClients(endpoint, data) {
    // Use multiroomStore for availability-aware propagation
    const registryStore = useMultiroomStore();

    // Get linked clients that are available
    const linkedIds = registryStore.getLinkedClientIds(selectedTarget.value);
    if (linkedIds.length <= 1) return { success: true, errors: [], skipped: [] };

    // Filter to only online clients (skip offline ones)
    const otherClients = linkedIds.filter(id => id !== selectedTarget.value);
    const onlineClients = otherClients.filter(id => registryStore.isClientOnline(id));
    const skippedClients = otherClients.filter(id => !registryStore.isClientOnline(id));

    if (skippedClients.length > 0) {
      logger.debug('store', `Skipping offline clients for ${endpoint}`, skippedClients);
    }

    const errors = [];
    const promises = onlineClients.map(async (targetId) => {
      try {
        // Special handling for preset: URL format is /preset/{preset_id}
        if (endpoint === 'preset' && data.preset_id) {
          await axios.put(`${getApiBase(targetId)}/preset/${data.preset_id}`);
        } else {
          await axios.put(`${getApiBase(targetId)}/${endpoint}`, data);
        }
      } catch (error) {
        const errorMsg = error.response?.data?.detail || error.message || 'Unknown error';
        logger.error('store', `Error propagating ${endpoint} to ${targetId}`, error);
        errors.push({ targetId, endpoint, error: errorMsg });
      }
    });

    await Promise.all(promises);

    // Track errors for UI notification
    if (errors.length > 0) {
      addPropagationErrors(errors.map(e => ({
        clientId: e.targetId,
        setting: endpoint,
        error: e.error
      })));
    }

    return { success: errors.length === 0, errors, skipped: skippedClients };
  }

  /**
   * Add propagation errors to the list (for UI notification)
   */
  function addPropagationErrors(newErrors) {
    const now = Date.now();
    const errorEntries = newErrors.map(e => ({
      ...e,
      timestamp: now
    }));
    propagationErrors.value = [...propagationErrors.value, ...errorEntries];

    // Auto-clear errors after 10 seconds
    if (errorClearTimeout) {
      clearTimeout(errorClearTimeout);
    }
    errorClearTimeout = setTimeout(() => {
      clearPropagationErrors();
    }, 10000);
  }

  /**
   * Clear all propagation errors
   */
  function clearPropagationErrors() {
    propagationErrors.value = [];
    if (errorClearTimeout) {
      clearTimeout(errorClearTimeout);
      errorClearTimeout = null;
    }
  }

  /**
   * Get friendly client name for error display
   */
  function getClientDisplayName(clientId) {
    return registryStore.getClientName(clientId);
  }

  // === ACTIONS ===
  async function loadStatus() {
    // Cancel previous request if it exists
    if (loadAbortController) {
      loadAbortController.abort();
    }
    loadAbortController = new AbortController();
    const signal = loadAbortController.signal;

    isLoading.value = true;
    filtersLoaded.value = false;

    try {
      const [statusData, filtersData, presetsData] = await Promise.all([
        fetchStatus(signal),
        fetchFilters(signal),
        fetchPresets()
      ]);

      // Check if request was cancelled
      if (statusData === null || filtersData === null) {
        return;
      }

      // Update state
      state.value = statusData?.state || 'disconnected';
      sampleRate.value = statusData?.sample_rate || 48000;

      // Update filters
      if (filtersData.length > 0) {
        filters.value = filtersData.map(f => ({
          ...f,
          displayName: formatFrequency(f.freq)
        }));
      } else {
        initializeFilters();
      }

      // Presets are already updated by fetchPresets()

      // Update advanced settings from status (preserve defaults for missing fields)
      if (statusData?.compressor) {
        compressor.value = { ...compressor.value, ...statusData.compressor };
      }
      if (statusData?.loudness) {
        loudness.value = { ...loudness.value, ...statusData.loudness };
      }

      // When in a zone, the zone registry is the source of truth for equalizer settings.
      // The local CamillaDSP cache may be stale (zone operations use persist=False).
      // Override loudness/compressor with zone data to prevent state desync.
      const zoneId = getSelectedZoneId();
      if (zoneId) {
        const zoneEq = await fetchZoneEqualizer(zoneId, signal);
        if (zoneEq) {
          if (zoneEq.loudness) {
            loudness.value = { ...loudness.value, ...zoneEq.loudness };
          }
          if (zoneEq.compressor) {
            compressor.value = { ...compressor.value, ...zoneEq.compressor };
          }
        }
      }

      // Volume data comes from unifiedAudioStore.volumeState via WebSocket
      // No need to update local cache here

      filtersLoaded.value = true;
    } catch (error) {
      if (axios.isCancel(error) || error.name === 'CanceledError') {
        return;
      }
      logger.error('store', 'Error loading equalizer data', error);
    } finally {
      isLoading.value = false;
      loadAbortController = null;
    }
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

  async function resetAllFilters() {
    if (isResetting.value) return false;

    isResetting.value = true;
    try {
      const success = await sendResetFilters();
      if (success) {
        filters.value.forEach(filter => {
          filter.gain = 0;
        });

        // Propagate reset to online linked clients
        const registryStore = useMultiroomStore();
        const linkedIds = registryStore.getLinkedClientIds(selectedTarget.value);
        if (linkedIds.length > 1) {
          // Only propagate to online clients
          const onlineClients = linkedIds.filter(id =>
            id !== selectedTarget.value && registryStore.isClientOnline(id)
          );
          const promises = onlineClients.map(async (targetId) => {
            try {
              await axios.post(`${getApiBase(targetId)}/reset`);
            } catch (error) {
              logger.error('store', `Error resetting filters on ${targetId}`, error);
            }
          });
          await Promise.all(promises);
        }
      }
      return success;
    } catch (error) {
      logger.error('store', 'Error resetting filters', error);
      return false;
    } finally {
      isResetting.value = false;
    }
  }

  // === PRESET MANAGEMENT ===
  async function loadPreset(presetId) {
    isLoadingPreset.value = true;
    try {
      // If target is in a zone, use zone endpoint (backend handles propagation)
      const zoneId = getSelectedZoneId();
      if (zoneId) {
        const response = await axios.post(`/api/equalizer/zone/${zoneId}/preset`, { preset_id: presetId });
        if (response.data.status === 'success' || response.data.status === 'partial') {
          activePreset.value = presetId;
          // WebSocket filter_changed events update filters.value automatically
          return true;
        }
        return false;
      }

      // Standalone client: update directly
      const response = await axios.put(`/api/equalizer/preset/${presetId}`);
      if (response.data.status === 'success') {
        activePreset.value = presetId;
        // WebSocket filter_changed events update filters.value automatically
        return true;
      }
      return false;
    } catch (error) {
      logger.error('store', 'Error loading preset', error);
      return false;
    } finally {
      isLoadingPreset.value = false;
    }
  }

  // === ADVANCED FEATURES ===

  async function updateCompressor(settings) {
    try {
      // If target is in a zone, use zone endpoint (backend handles propagation)
      const zoneId = getSelectedZoneId();
      if (zoneId) {
        const response = await axios.patch(`/api/equalizer/zone/${zoneId}/compressor`, settings);
        if (response.data.status === 'success' || response.data.status === 'partial') {
          Object.assign(compressor.value, settings);
          return true;
        }
        return false;
      }

      // Standalone client: update directly
      const response = await axios.put(`${getApiBase()}/compressor`, settings);
      if (response.data.status === 'success') {
        Object.assign(compressor.value, settings);
        return true;
      }
      return false;
    } catch (error) {
      logger.error('store', 'Error updating compressor', error);
      return false;
    }
  }

  async function updateLoudness(settings) {
    try {
      // If target is in a zone, use zone endpoint (backend handles propagation)
      const zoneId = getSelectedZoneId();
      if (zoneId) {
        const response = await axios.patch(`/api/equalizer/zone/${zoneId}/loudness`, settings);
        if (response.data.status === 'success' || response.data.status === 'partial') {
          Object.assign(loudness.value, settings);
          return true;
        }
        return false;
      }

      // Standalone client: update directly
      const response = await axios.put(`${getApiBase()}/loudness`, settings);
      if (response.data.status === 'success') {
        Object.assign(loudness.value, settings);
        return true;
      }
      return false;
    } catch (error) {
      logger.error('store', 'Error updating loudness', error);
      return false;
    }
  }

  async function updateEqualizerMute(muted) {
    try {
      const response = await axios.put(`${getApiBase()}/mute`, { muted });
      if (response.data.status === 'success') {
        // Propagate mute to all available linked clients in the zone
        const registryStore = useMultiroomStore();
        const linkedIds = registryStore.getLinkedClientIds(selectedTarget.value);
        if (linkedIds.length > 1) {
          // Propagate to available remote clients
          await propagateToLinkedClients('mute', { muted });
        }
        // Unified state will be updated via WebSocket broadcast
        return true;
      }
      return false;
    } catch (error) {
      logger.error('store', 'Error updating equalizer mute', error);
      return false;
    }
  }

  // === TARGET MANAGEMENT ===

  async function loadTargets() {
    // Ensure multiroomStore is initialized
    // availableTargets is now a computed property that delegates to multiroomStore
    if (!registryStore.isInitialized) {
      await registryStore.initialize();
    }
  }

  async function selectTarget(targetId) {
    if (targetId === selectedTarget.value) return;

    // Clear current state
    cleanup();
    selectedTarget.value = targetId;

    // For remote clients, restore saved settings from Milo first
    if (targetId && !isLocalClient(targetId)) {
      await restoreClientSettings(targetId);
    }

    // Load status for new target
    await loadStatus();
  }

  async function restoreClientSettings(hostname) {
    try {
      const response = await axios.post(`/api/equalizer/client/${hostname}/restore`);
      if (response.data.restored && response.data.restored.length > 0) {
        logger.info('store', `Restored equalizer settings for ${hostname}`, response.data.restored);
      }
      return response.data;
    } catch (error) {
      logger.error('store', `Error restoring settings for ${hostname}`, error);
      return null;
    }
  }

  // === LINKED CLIENTS MANAGEMENT ===

  async function linkClients(clientIds, sourceClient = null, zoneName = null) {
    try {
      // Delegate to multiroomStore - single source of truth for zones
      const response = await registryStore.createZone(zoneName || '', clientIds);
      // Response includes zone data if successful
      return !!response.zone;
    } catch (error) {
      logger.error('store', 'Error linking clients', error);
      return false;
    }
  }

  async function unlinkClient(clientId) {
    try {
      // Find the zone this client belongs to
      const zone = registryStore.getZoneForClient(clientId);
      if (!zone) {
        // Client not in any zone, nothing to unlink
        return true;
      }
      // Delegate to multiroomStore
      await registryStore.removeClientFromZone(zone.id, clientId);
      return true;
    } catch (error) {
      logger.error('store', 'Error unlinking client', error);
      return false;
    }
  }

  async function clearAllLinks() {
    try {
      // Delete all zones via multiroomStore
      const allZones = [...registryStore.zoneList];
      for (const zone of allZones) {
        await registryStore.deleteZone(zone.id);
      }
      return true;
    } catch (error) {
      logger.error('store', 'Error clearing links', error);
      return false;
    }
  }

  async function deleteZone(groupId) {
    try {
      // Delegate to multiroomStore
      await registryStore.deleteZone(groupId);
      return true;
    } catch (error) {
      logger.error('store', 'Error deleting zone', error);
      return false;
    }
  }

  async function updateZoneName(groupId, name) {
    try {
      // Delegate to multiroomStore
      await registryStore.updateZone(groupId, { name });
      return true;
    } catch (error) {
      logger.error('store', 'Error updating zone name', error);
      return false;
    }
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
   * Get the crossover frequency for a client
   * @param {string} clientId - Client ID (mac_id)
   * @returns {number|null} Crossover frequency in Hz, or null for subwoofer
   */
  function getClientCrossoverFrequency(clientId) {
    const clientData = clientTypes.value[clientId];
    if (clientData?.crossover_frequency !== undefined) {
      return clientData.crossover_frequency;
    }
    // Return default based on speaker type
    const speakerType = getClientSpeakerType(clientId);
    return DEFAULT_CROSSOVER_FREQUENCIES[speakerType] ?? 80;
  }

  /**
   * Check if a client is marked as a subwoofer (derived from speaker_type)
   * @param {string} clientId - Client ID (mac_id)
   * @returns {boolean} True if client is a subwoofer
   */
  function isClientSubwoofer(clientId) {
    return getClientSpeakerType(clientId) === 'subwoofer';
  }

  /**
   * Set the speaker type for a client
   * @param {string} clientId - Client ID (mac_id)
   * @param {string} speakerType - 'satellite', 'bookshelf', 'tower', or 'subwoofer'
   * @returns {Promise<boolean>} Success status
   */
  async function setClientSpeakerType(clientId, speakerType) {
    try {
      // Delegate to multiroomStore
      await registryStore.updateClient(clientId, { speaker_type: speakerType });
      return true;
    } catch (error) {
      logger.error('store', 'Error setting client speaker type', error);
      return false;
    }
  }

  /**
   * Set custom crossover frequency for a client
   * @param {string} clientId - Client ID (mac_id)
   * @param {number} frequency - Crossover frequency in Hz (20-200)
   * @returns {Promise<boolean>} Success status
   */
  async function setClientCrossoverFrequency(clientId, frequency) {
    try {
      const response = await axios.put(`/api/equalizer/client/${clientId}/crossover-frequency`, {
        frequency
      });
      // State update happens via WebSocket (registry.speaker_type_changed)
      return response.data.status === 'success';
    } catch (error) {
      logger.error('store', 'Error setting client crossover frequency', error);
      return false;
    }
  }

  /**
   * Check if a zone has a subwoofer
   * @param {string} zoneId - Zone ID
   * @returns {boolean} True if zone contains a subwoofer client
   */
  function hasSubwooferInZone(zoneId) {
    const zone = linkedGroups.value.find(g => g.id === zoneId);
    if (!zone) return false;
    return zone.client_ids?.some(clientId => isClientSubwoofer(clientId)) || false;
  }

  /**
   * Get crossover settings for a zone
   * @param {string} zoneId - Zone ID
   * @returns {Object} Crossover settings { frequency, enabled, has_subwoofer }
   */
  function getZoneCrossoverSettings(zoneId) {
    return zoneCrossover.value[zoneId] || { frequency: 80, enabled: false, has_subwoofer: false };
  }

  /**
   * Get auto-calculated crossover frequency for a zone from API
   * @param {string} zoneId - Zone ID
   * @returns {Promise<number>} Crossover frequency in Hz
   */
  async function getZoneAutoCrossover(zoneId) {
    try {
      const response = await axios.get(`/api/equalizer/links/${zoneId}/auto-crossover`);
      return response.data.frequency || 80;
    } catch (error) {
      logger.error('store', 'Error getting zone auto crossover', error);
      return 80;
    }
  }

  /**
   * Update crossover frequency for a zone
   * @param {string} zoneId - Zone ID
   * @param {number} frequency - Crossover frequency in Hz (40-200)
   * @returns {Promise<boolean>} Success status
   */
  async function setZoneCrossoverFrequency(zoneId, frequency) {
    try {
      const response = await axios.put(`/api/equalizer/links/${zoneId}/crossover`, { frequency });
      if (response.data.status === 'success') {
        zoneCrossover.value[zoneId] = {
          ...zoneCrossover.value[zoneId],
          frequency: response.data.frequency,
          enabled: response.data.enabled,
          has_subwoofer: response.data.has_subwoofer
        };
        return true;
      }
      return false;
    } catch (error) {
      logger.error('store', 'Error setting zone crossover', error);
      return false;
    }
  }

  /**
   * Manually apply crossover to all clients in a zone
   * @param {string} zoneId - Zone ID
   * @returns {Promise<boolean>} Success status
   */
  async function applyZoneCrossover(zoneId) {
    try {
      const response = await axios.post(`/api/equalizer/links/${zoneId}/crossover/apply`);
      return response.data.status === 'success';
    } catch (error) {
      logger.error('store', 'Error applying zone crossover', error);
      return false;
    }
  }

  /**
   * Handle zone crossover changed events.
   * Supports both legacy format (crossover.zone_crossover_changed) and
   * new multiroom format (multiroom.crossover_changed).
   * @param {Object} event - Event with data containing zone crossover info
   *   New format: { zone_id, crossover_enabled, crossover_frequency }
   *   Legacy format: { zone_id, frequency, enabled, has_subwoofer }
   */
  function handleZoneCrossoverChanged(event) {
    if (event.data) {
      const data = event.data;

      // Support both old and new field names
      const zoneId = data.zone_id;
      const frequency = data.crossover_frequency ?? data.frequency;
      const enabled = data.crossover_enabled ?? data.enabled;
      const hasSubwoofer = data.has_subwoofer ?? false;

      if (zoneId) {
        zoneCrossover.value[zoneId] = {
          frequency,
          enabled,
          has_subwoofer: hasSubwoofer
        };
      }
    }
  }

  // === WEBSOCKET HANDLERS ===

  /**
   * Handle equalizer changed events from multiroom category.
   * Updates local equalizer state when the target matches selectedTarget.
   * @param {Object} event - WebSocket event with data:
   *   { target_type: "zone"|"client", target_id, equalizer_settings }
   *   equalizer_settings may contain: filters, compressor, loudness
   */
  function handleEqualizerChanged(event) {
    if (!event.data) return;

    const { target_type, target_id, equalizer_settings } = event.data;
    if (!equalizer_settings) return;

    // ALWAYS update activePreset for zone events if we have a client in that zone
    // This must happen BEFORE the relevance check for filter updates, because
    // the relevance check may fail while the preset change is still valid
    if (target_type === 'zone' && equalizer_settings.active_preset !== undefined) {
      const zone = registryStore.getZoneForClient(selectedTarget.value);
      if (zone && zone.id === target_id) {
        activePreset.value = equalizer_settings.active_preset;
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

    // Update active preset if present in the event
    if (equalizer_settings.active_preset !== undefined) {
      activePreset.value = equalizer_settings.active_preset;
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

  function handleFiltersReset() {
    // Don't update if throttling is in progress
    if (filterThrottleMap.size === 0) {
      filters.value.forEach(filter => {
        filter.gain = 0;
      });
    }
  }

  function handleStateChanged(event) {
    state.value = event.data.state || 'disconnected';
  }

  function handlePresetLoaded(event) {
    const presetId = event.data.id || event.data.name;
    activePreset.value = presetId;

    // Apply preset gains to filters (since individual filter_changed events are suppressed)
    let gains = null;
    if (presetId === 'manual') {
      gains = manualGains.value;
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

  function handleLevels(event) {
    inputPeak.value = event.data.input_peak || [-80, -80];
    outputPeak.value = event.data.output_peak || [-80, -80];
  }

  function updateLevels(input, output) {
    inputPeak.value = input;
    outputPeak.value = output;
  }

  function handleCompressorChanged(event) {
    Object.assign(compressor.value, event.data);
  }

  function handleLoudnessChanged(event) {
    Object.assign(loudness.value, event.data);
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
    activePreset.value = 'manual';
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

    try {
      let success = false;

      // If target is in a zone, use zone endpoint (backend handles propagation)
      const zoneId = getSelectedZoneId();
      if (zoneId) {
        try {
          const response = await axios.patch(`/api/equalizer/zone/${zoneId}/enabled`, { enabled });
          success = response.data.status === 'success' || response.data.status === 'partial';
        } catch (error) {
          logger.error('store', 'Error updating zone equalizer enabled', error);
          // Fall back to direct update
          success = await setEnabledState(enabled);
        }
      } else {
        // Standalone client: update directly
        success = await setEnabledState(enabled);
      }

      if (success) {
        if (enabled) {
          // Equalizer effects enabled: load status
          await loadStatus();
        } else {
          // Equalizer effects disabled: cleanup local state
          cleanup();
        }
        return true;
      } else {
        // Revert on failure
        isEqualizerEffectsEnabled.value = previousState;
        return false;
      }
    } catch (error) {
      logger.error('store', 'Error toggling equalizer effects', error);
      isEqualizerEffectsEnabled.value = previousState;
      return false;
    } finally {
      isTogglingEnabled.value = false;
    }
  }

  function handleEnabledChanged(event) {
    if (event.data && event.data.enabled !== undefined) {
      isEqualizerEffectsEnabled.value = event.data.enabled;
    }
  }

  return {
    // State
    filters,
    activePreset,
    state,
    isLoading,
    isUpdating,
    isResetting,
    filtersLoaded,
    sampleRate,
    inputPeak,
    outputPeak,

    // Equalizer Effects Enabled State
    isEqualizerEffectsEnabled,
    isTogglingEnabled,

    // Advanced Equalizer State
    compressor,
    loudness,

    // Multi-client support
    selectedTarget,
    availableTargets,
    linkedGroups,

    // Computed
    isConnected,
    isRunning,

    // Utils
    formatFrequency,

    // Actions
    initializeFilters,
    loadStatus,
    updateFilter,
    finalizeFilterUpdate,
    resetAllFilters,
    cleanup,

    // Equalizer Effects Enable/Disable
    loadEnabledState,
    toggleEqualizerEffectsEnabled,

    // Target Management
    loadTargets,
    selectTarget,

    // Linked Clients Management
    linkClients,
    unlinkClient,
    clearAllLinks,
    deleteZone,
    updateZoneName,
    isClientLinked,
    getLinkedClientIds,
    getZoneName,
    getZoneGroup,
    getSelectedZoneId,
    isTargetInZone,

    // Speaker Type / Crossover Management
    clientTypes,
    zoneCrossover,
    DEFAULT_CROSSOVER_FREQUENCIES,
    getClientSpeakerType,
    getClientCrossoverFrequency,
    setClientSpeakerType,
    setClientCrossoverFrequency,
    isClientSubwoofer,
    hasSubwooferInZone,
    getZoneCrossoverSettings,
    getZoneAutoCrossover,
    setZoneCrossoverFrequency,
    applyZoneCrossover,
    handleZoneCrossoverChanged,

    // Preset Management
    builtinPresets,
    manualGains,
    isManualMode,
    loadPreset,

    // Advanced Features
    updateCompressor,
    updateLoudness,
    updateEqualizerMute,

    // Client equalizer volume/mute (reads from unified store)
    updateClientEqualizerVolume,
    applyZoneDelta,  // Atomic zone volume update
    getClientEqualizerVolume,
    getClientEqualizerMute,
    updateClientEqualizerMute,  // Use { propagate: true } for zone propagation

    // Propagation Errors
    propagationErrors,
    clearPropagationErrors,
    getClientDisplayName,

    // WebSocket Handlers
    handleEqualizerChanged,
    handleFilterChanged,
    handleFiltersReset,
    handleStateChanged,
    handlePresetLoaded,
    handleLevels,
    updateLevels,
    handleCompressorChanged,
    handleLoudnessChanged,
    handleEnabledChanged
  };
});
