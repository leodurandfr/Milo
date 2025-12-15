// frontend/src/stores/dspStore.js
/**
 * Pinia store for CamillaDSP parametric equalizer
 * Manages DSP state, filters, and presets
 */
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import axios from 'axios';
import { useSettingsStore } from './settingsStore';

// Default 10-band parametric EQ frequencies
const DEFAULT_FREQUENCIES = [31, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000];

// Throttle settings for real-time updates
const THROTTLE_DELAY = 50;
const FINAL_DELAY = 200;

// Filter type options
export const FILTER_TYPES = [
  { value: 'Peaking', label: 'Peaking' },
  { value: 'Lowshelf', label: 'Low Shelf' },
  { value: 'Highshelf', label: 'High Shelf' },
  { value: 'Lowpass', label: 'Low Pass' },
  { value: 'Highpass', label: 'High Pass' },
  { value: 'Notch', label: 'Notch' },
  { value: 'Allpass', label: 'All Pass' }
];

export const useDspStore = defineStore('dsp', () => {
  // === STATE ===
  const filters = ref([]);
  const presets = ref([]);
  const activePreset = ref(null);
  const state = ref('disconnected'); // disconnected, inactive, running, paused
  const isLoading = ref(false);
  const isUpdating = ref(false);
  const isResetting = ref(false);
  const filtersLoaded = ref(false);
  const sampleRate = ref(48000);

  // DSP effects enabled state (persisted in settings)
  // Note: Volume always works via CamillaDSP, this controls EQ/compressor/loudness
  const isDspEffectsEnabled = ref(true);
  const isTogglingEnabled = ref(false);

  // Audio levels (for meters)
  const inputPeak = ref([0, 0]);
  const outputPeak = ref([0, 0]);

  // Advanced DSP settings
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
    reference_level: 80,
    high_boost: 5,
    low_boost: 8
  });

  const delay = ref({
    left: 0,
    right: 0
  });

  // Unified client DSP state cache: { hostname: { volume, mute } }
  // Single source of truth for volume and mute state per client
  // Keys are normalized hostnames ('local' for main Milo, 'milo-client-01' for remotes)
  const clientDspState = ref({});

  // Computed properties for current target (used by DSP Modal)
  const currentTargetVolume = computed(() =>
    clientDspState.value[selectedTarget.value]?.volume ?? -30
  );
  const currentTargetMute = computed(() =>
    clientDspState.value[selectedTarget.value]?.mute ?? false
  );

  // Multi-client DSP support
  // 'local' = main Milo, or client hostname like 'milo-client-01'
  const selectedTarget = ref('local');
  const availableTargets = ref([
    { id: 'local', name: 'Milo', host: 'local', available: true }
  ]);

  // Linked clients - clients that share DSP settings
  // Structure: [{ id: 'group_1', client_ids: ['local', 'milo-client-01'] }]
  const linkedGroups = ref([]);

  // AbortController for cancelling ongoing requests
  let loadAbortController = null;

  // Throttling management
  const filterThrottleMap = new Map();

  // === COMPUTED ===
  const isAvailable = computed(() => state.value !== 'disconnected');
  const isConnected = computed(() => state.value !== 'disconnected');
  const isRunning = computed(() => state.value === 'running');

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
  function getApiBase() {
    // If targeting a remote client, use proxy endpoint
    if (selectedTarget.value && selectedTarget.value !== 'local') {
      return `/api/dsp/client/${selectedTarget.value}`;
    }
    return '/api/dsp';
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
      console.error('Error fetching DSP status:', error);
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
      console.error('Error fetching DSP filters:', error);
      return [];
    }
  }

  async function fetchPresets() {
    try {
      // Presets are always stored locally on Milo, not on clients
      const response = await axios.get('/api/dsp/presets');
      return response.data.presets || [];
    } catch (error) {
      console.error('Error fetching DSP presets:', error);
      return [];
    }
  }

  async function sendFilterUpdate(filterId, filterData) {
    try {
      const response = await axios.put(`${getApiBase()}/filter/${filterId}`, filterData);
      return response.data.status === 'success';
    } catch (error) {
      console.error('Error updating filter:', error);
      return false;
    }
  }

  async function sendResetFilters() {
    try {
      const response = await axios.post(`${getApiBase()}/reset`);
      return response.data.status === 'success';
    } catch (error) {
      console.error('Error resetting filters:', error);
      return false;
    }
  }

  async function fetchAvailableTargets() {
    try {
      const response = await axios.get('/api/dsp/targets');
      return response.data.targets || [];
    } catch (error) {
      console.error('Error fetching DSP targets:', error);
      return [{ id: 'local', name: 'Milo', host: 'local', available: true }];
    }
  }

  async function fetchLinkedGroups() {
    try {
      const response = await axios.get('/api/dsp/links');
      return response.data.linked_groups || [];
    } catch (error) {
      console.error('Error fetching linked groups:', error);
      return [];
    }
  }

  async function fetchEnabledState() {
    try {
      const response = await axios.get('/api/dsp/enabled');
      return response.data.enabled ?? true;
    } catch (error) {
      console.error('Error fetching DSP enabled state:', error);
      return true;
    }
  }

  async function setEnabledState(enabled) {
    try {
      const response = await axios.put('/api/dsp/enabled', { enabled });
      return response.data.status === 'success';
    } catch (error) {
      console.error('Error setting DSP enabled state:', error);
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

  // Get API base for a specific target (for propagation)
  function getApiBaseForTarget(targetId) {
    if (targetId && targetId !== 'local') {
      return `/api/dsp/client/${targetId}`;
    }
    return '/api/dsp';
  }

  // Normalize hostname: 'milo' -> 'local' for consistency
  function normalizeHostname(hostname) {
    return hostname === 'milo' ? 'local' : hostname;
  }

  // === CLIENT DSP VOLUMES ===

  /**
   * Get DSP volume for a specific client
   * @param {string} hostname - Client hostname (can be 'milo' or 'local')
   * @returns {Promise<number|null>} Volume in dB or null on error
   */
  async function fetchClientDspVolume(hostname) {
    try {
      const normalized = normalizeHostname(hostname);
      // All clients (including local) use the unified DSP client endpoint
      const response = await axios.get(`/api/dsp/client/${normalized}/volume`);
      return response.data.main ?? response.data.volume ?? null;
    } catch (error) {
      console.error(`Error fetching DSP volume for ${hostname}:`, error);
      return null;
    }
  }

  /**
   * Load DSP volumes for all clients and cache them
   * @param {Array} clients - Array of client objects with host property
   */
  async function loadAllClientDspVolumes(clients) {
    const promises = clients.map(async (client) => {
      // Use dsp_id as the canonical key (matches zone.client_ids)
      const hostname = client.dsp_id || (client.host === 'milo' ? 'local' : (client.ip || client.host || ''));
      const volume = await fetchClientDspVolume(hostname);
      if (volume !== null) {
        setClientDspVolume(hostname, volume);
      }
    });
    await Promise.all(promises);
  }

  /**
   * Update DSP volume for a client via API
   * Uses unified endpoint for all clients (local and remote).
   * Each client's volume is independent - changing one doesn't affect others.
   * @param {string} hostname - Client hostname
   * @param {number} volumeDb - Volume in dB (-60 to 0)
   * @returns {Promise<boolean>} Success status
   */
  async function updateClientDspVolume(hostname, volumeDb) {
    try {
      const normalized = normalizeHostname(hostname);
      // Unified endpoint for all clients (local and remote)
      // Backend handles 'local' specially via multiroom_handler.set_client_volume_db()
      await axios.put(`/api/dsp/client/${normalized}/volume`, { volume: volumeDb });
      return true;
    } catch (error) {
      console.error(`Error updating DSP volume for ${hostname}:`, error);
      return false;
    }
  }

  /**
   * Set DSP volume in local cache (after successful API update)
   * @param {string} hostname - Client hostname (can be 'milo' or 'local')
   * @param {number} volumeDb - Volume in dB (-60 to 0)
   */
  function setClientDspVolume(hostname, volumeDb) {
    const normalized = normalizeHostname(hostname);
    if (!clientDspState.value[normalized]) {
      clientDspState.value[normalized] = { volume: volumeDb, mute: false };
    } else {
      clientDspState.value[normalized].volume = volumeDb;
    }
  }

  /**
   * Set DSP mute in local cache
   * @param {string} hostname - Client hostname
   * @param {boolean} muted - Mute state
   */
  function setClientDspMute(hostname, muted) {
    const normalized = normalizeHostname(hostname);
    if (!clientDspState.value[normalized]) {
      clientDspState.value[normalized] = { volume: -30, mute: muted };
    } else {
      clientDspState.value[normalized].mute = muted;
    }
  }

  /**
   * Get cached DSP volume for a client
   * @param {string} hostname - Client hostname
   * @returns {number} Volume in dB, defaults to -30 if not cached
   */
  function getClientDspVolume(hostname) {
    const normalized = normalizeHostname(hostname);
    return clientDspState.value[normalized]?.volume ?? -30;
  }

  /**
   * Get cached DSP mute for a client
   * @param {string} hostname - Client hostname
   * @returns {boolean} Mute state, defaults to false if not cached
   */
  function getClientDspMute(hostname) {
    const normalized = normalizeHostname(hostname);
    return clientDspState.value[normalized]?.mute ?? false;
  }

  /**
   * Clear all cached client DSP state
   */
  function clearClientDspVolumes() {
    clientDspState.value = {};
  }

  /**
   * Apply global volume delta to all cached client volumes
   * Called when volume is changed via Dock buttons or rotary encoder
   * @param {number} deltaDb - Volume delta in dB
   */
  function applyGlobalDelta(deltaDb) {
    const settingsStore = useSettingsStore();
    const limits = settingsStore.volumeLimits;

    for (const hostname of Object.keys(clientDspState.value)) {
      const current = clientDspState.value[hostname]?.volume ?? -30;
      const newVolume = Math.max(limits.min_db, Math.min(limits.max_db, current + deltaDb));
      clientDspState.value[hostname].volume = newVolume;
    }
  }

  // Propagate a filter update to linked clients
  async function propagateFilterToLinkedClients(filterId, filterData) {
    const linkedIds = getLinkedClientIds(selectedTarget.value);
    if (linkedIds.length <= 1) return; // No linked clients

    const promises = linkedIds
      .filter(id => id !== selectedTarget.value)
      .map(async (targetId) => {
        try {
          await axios.put(`${getApiBaseForTarget(targetId)}/filter/${filterId}`, filterData);
        } catch (error) {
          console.error(`Error propagating filter to ${targetId}:`, error);
        }
      });

    await Promise.all(promises);
  }

  // Propagate any DSP setting to linked clients
  async function propagateToLinkedClients(endpoint, data) {
    const linkedIds = getLinkedClientIds(selectedTarget.value);
    if (linkedIds.length <= 1) return; // No linked clients

    const promises = linkedIds
      .filter(id => id !== selectedTarget.value)
      .map(async (targetId) => {
        try {
          await axios.put(`${getApiBaseForTarget(targetId)}/${endpoint}`, data);
        } catch (error) {
          console.error(`Error propagating ${endpoint} to ${targetId}:`, error);
        }
      });

    await Promise.all(promises);
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

      // Update presets
      presets.value = presetsData;

      // Update advanced settings from status
      if (statusData?.compressor) {
        compressor.value = statusData.compressor;
      }
      if (statusData?.loudness) {
        loudness.value = statusData.loudness;
      }
      if (statusData?.delay) {
        delay.value = statusData.delay;
      }
      if (statusData?.volume) {
        // Update the unified cache for the selected target
        setClientDspVolume(selectedTarget.value, statusData.volume.main ?? statusData.volume);
        if (statusData.volume.mute !== undefined) {
          setClientDspMute(selectedTarget.value, statusData.volume.mute);
        }
      }

      filtersLoaded.value = true;
    } catch (error) {
      if (axios.isCancel(error) || error.name === 'CanceledError') {
        return;
      }
      console.error('Error loading DSP data:', error);
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
      throttleState.lastRequestTime = Date.now();
    }, FINAL_DELAY);

    filterThrottleMap.set(filterId, throttleState);
  }

  function clearThrottleForFilter(filterId) {
    const throttleState = filterThrottleMap.get(filterId);
    if (throttleState) {
      if (throttleState.throttleTimeout) clearTimeout(throttleState.throttleTimeout);
      if (throttleState.finalTimeout) clearTimeout(throttleState.finalTimeout);
      filterThrottleMap.delete(filterId);
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
      await sendFilterUpdate(filterId, filterData);
      clearThrottleForFilter(filterId);

      // Propagate to linked clients
      await propagateFilterToLinkedClients(filterId, filterData);
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

        // Propagate reset to linked clients
        const linkedIds = getLinkedClientIds(selectedTarget.value);
        if (linkedIds.length > 1) {
          const promises = linkedIds
            .filter(id => id !== selectedTarget.value)
            .map(async (targetId) => {
              try {
                await axios.post(`${getApiBaseForTarget(targetId)}/reset`);
              } catch (error) {
                console.error(`Error resetting filters on ${targetId}:`, error);
              }
            });
          await Promise.all(promises);
        }
      }
      return success;
    } catch (error) {
      console.error('Error resetting filters:', error);
      return false;
    } finally {
      isResetting.value = false;
    }
  }

  // === PRESET MANAGEMENT ===
  async function savePreset(name) {
    try {
      const response = await axios.post('/api/dsp/preset', { name });
      if (response.data.status === 'success') {
        if (!presets.value.includes(name)) {
          presets.value.push(name);
        }
        activePreset.value = name;
        return true;
      }
      return false;
    } catch (error) {
      console.error('Error saving preset:', error);
      return false;
    }
  }

  async function loadPreset(name) {
    try {
      const response = await axios.put(`/api/dsp/preset/${name}`);
      if (response.data.status === 'success') {
        activePreset.value = name;
        // Reload filters after preset load
        await loadStatus();
        return true;
      }
      return false;
    } catch (error) {
      console.error('Error loading preset:', error);
      return false;
    }
  }

  async function deletePreset(name) {
    try {
      const response = await axios.delete(`/api/dsp/preset/${name}`);
      if (response.data.status === 'success') {
        presets.value = presets.value.filter(p => p !== name);
        if (activePreset.value === name) {
          activePreset.value = null;
        }
        return true;
      }
      return false;
    } catch (error) {
      console.error('Error deleting preset:', error);
      return false;
    }
  }

  // === ADVANCED FEATURES ===

  async function updateCompressor(settings) {
    try {
      const response = await axios.put(`${getApiBase()}/compressor`, settings);
      if (response.data.status === 'success') {
        Object.assign(compressor.value, settings);
        // Propagate to linked clients
        await propagateToLinkedClients('compressor', settings);
        return true;
      }
      return false;
    } catch (error) {
      console.error('Error updating compressor:', error);
      return false;
    }
  }

  async function updateLoudness(settings) {
    try {
      const response = await axios.put(`${getApiBase()}/loudness`, settings);
      if (response.data.status === 'success') {
        Object.assign(loudness.value, settings);
        // Propagate to linked clients
        await propagateToLinkedClients('loudness', settings);
        return true;
      }
      return false;
    } catch (error) {
      console.error('Error updating loudness:', error);
      return false;
    }
  }

  async function updateDelay(settings) {
    try {
      const response = await axios.put(`${getApiBase()}/delay`, settings);
      if (response.data.status === 'success') {
        Object.assign(delay.value, settings);
        // Propagate to linked clients
        await propagateToLinkedClients('delay', settings);
        return true;
      }
      return false;
    } catch (error) {
      console.error('Error updating delay:', error);
      return false;
    }
  }

  async function updateDspMute(muted) {
    try {
      const response = await axios.put(`${getApiBase()}/mute`, { muted });
      if (response.data.status === 'success') {
        // Update unified cache for selected target
        setClientDspMute(selectedTarget.value, muted);
        // Mute is NOT propagated to linked clients (independent per client in zones)
        return true;
      }
      return false;
    } catch (error) {
      console.error('Error updating DSP mute:', error);
      return false;
    }
  }

  // === TARGET MANAGEMENT ===

  async function loadTargets() {
    const [targets, groups] = await Promise.all([
      fetchAvailableTargets(),
      fetchLinkedGroups()
    ]);
    if (targets.length > 0) {
      availableTargets.value = targets;
      // Load volumes for all targets
      const clients = targets.map(t => ({ dsp_id: t.id, host: t.host }));
      await loadAllClientDspVolumes(clients);
    }
    linkedGroups.value = groups;
  }

  async function selectTarget(targetId) {
    if (targetId === selectedTarget.value) return;

    // Clear current state
    cleanup();
    selectedTarget.value = targetId;

    // For remote clients, restore saved settings from Milo first
    if (targetId && targetId !== 'local') {
      await restoreClientSettings(targetId);
    }

    // Load status for new target
    await loadStatus();
  }

  async function restoreClientSettings(hostname) {
    try {
      const response = await axios.post(`/api/dsp/client/${hostname}/restore`);
      if (response.data.restored && response.data.restored.length > 0) {
        console.log(`Restored DSP settings for ${hostname}:`, response.data.restored);
      }
      return response.data;
    } catch (error) {
      console.error(`Error restoring settings for ${hostname}:`, error);
      return null;
    }
  }

  // === LINKED CLIENTS MANAGEMENT ===

  async function linkClients(clientIds, sourceClient = null, zoneName = null) {
    try {
      // Use the currently selected target as source if not specified
      const source = sourceClient || selectedTarget.value;
      const payload = {
        client_ids: clientIds,
        source_client: source
      };
      if (zoneName) {
        payload.name = zoneName;
      }
      const response = await axios.post('/api/dsp/links', payload);
      if (response.data.linked_groups) {
        linkedGroups.value = response.data.linked_groups;
        // Log sync results if available
        if (response.data.sync?.synced?.length > 0) {
          console.log('DSP settings synced:', response.data.sync.synced);
        }
        if (response.data.sync?.errors?.length > 0) {
          console.warn('DSP sync errors:', response.data.sync.errors);
        }
        return true;
      }
      return false;
    } catch (error) {
      console.error('Error linking clients:', error);
      return false;
    }
  }

  async function unlinkClient(clientId) {
    try {
      const response = await axios.delete(`/api/dsp/links/${clientId}`);
      if (response.data.linked_groups !== undefined) {
        linkedGroups.value = response.data.linked_groups;
        return true;
      }
      return false;
    } catch (error) {
      console.error('Error unlinking client:', error);
      return false;
    }
  }

  async function clearAllLinks() {
    try {
      const response = await axios.delete('/api/dsp/links');
      if (response.data.linked_groups !== undefined) {
        linkedGroups.value = [];
        return true;
      }
      return false;
    } catch (error) {
      console.error('Error clearing links:', error);
      return false;
    }
  }

  async function updateZoneName(groupId, name) {
    try {
      const response = await axios.put(`/api/dsp/links/${groupId}/name`, { name });
      if (response.data.linked_groups) {
        linkedGroups.value = response.data.linked_groups;
        return true;
      }
      return false;
    } catch (error) {
      console.error('Error updating zone name:', error);
      return false;
    }
  }

  function handleLinksChanged(event) {
    if (event.data && event.data.linked_groups !== undefined) {
      linkedGroups.value = event.data.linked_groups;
    }
  }

  // === WEBSOCKET HANDLERS ===
  function handleFilterChanged(event) {
    const { id, freq, gain, q, type } = event.data;
    const filter = filters.value.find(f => f.id === id);

    // Don't update if throttling is in progress (avoids conflicts)
    if (filter && filterThrottleMap.size === 0) {
      if (freq !== undefined) {
        filter.freq = freq;
        filter.displayName = formatFrequency(freq);
      }
      if (gain !== undefined) filter.gain = gain;
      if (q !== undefined) filter.q = q;
      if (type !== undefined) filter.type = type;
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
    activePreset.value = event.data.name;
  }

  function handleLevels(event) {
    inputPeak.value = event.data.input_peak || [0, 0];
    outputPeak.value = event.data.output_peak || [0, 0];
  }

  function handleCompressorChanged(event) {
    Object.assign(compressor.value, event.data);
  }

  function handleLoudnessChanged(event) {
    Object.assign(loudness.value, event.data);
  }

  function handleDelayChanged(event) {
    Object.assign(delay.value, event.data);
  }

  function handleMuteChanged(event) {
    if (event.data.muted !== undefined) {
      // Update unified cache for selected target
      setClientDspMute(selectedTarget.value, event.data.muted);
    }
  }

  // === CLEANUP ===
  function cleanup() {
    // Cancel pending requests
    if (loadAbortController) {
      loadAbortController.abort();
      loadAbortController = null;
    }
    clearAllThrottles();
    filtersLoaded.value = false;
  }

  // === DSP EFFECTS ENABLE/DISABLE ===
  async function loadEnabledState() {
    isDspEffectsEnabled.value = await fetchEnabledState();
    return isDspEffectsEnabled.value;
  }

  async function toggleDspEffectsEnabled(enabled) {
    if (isTogglingEnabled.value) return false;

    const previousState = isDspEffectsEnabled.value;
    isTogglingEnabled.value = true;
    isDspEffectsEnabled.value = enabled;

    try {
      const success = await setEnabledState(enabled);

      if (success) {
        if (enabled) {
          // DSP effects enabled: load status
          await loadStatus();
        } else {
          // DSP effects disabled: cleanup local state
          cleanup();
        }
        return true;
      } else {
        // Revert on failure
        isDspEffectsEnabled.value = previousState;
        return false;
      }
    } catch (error) {
      console.error('Error toggling DSP effects:', error);
      isDspEffectsEnabled.value = previousState;
      return false;
    } finally {
      isTogglingEnabled.value = false;
    }
  }

  function handleEnabledChanged(event) {
    if (event.data && event.data.enabled !== undefined) {
      isDspEffectsEnabled.value = event.data.enabled;
    }
  }

  function handleClientVolumesPushed(event) {
    const { volume_db, hostnames } = event.data;
    if (hostnames && volume_db !== undefined) {
      for (const hostname of hostnames) {
        setClientDspVolume(hostname, volume_db);
      }
    }
  }

  return {
    // State
    filters,
    presets,
    activePreset,
    state,
    isLoading,
    isUpdating,
    isResetting,
    filtersLoaded,
    sampleRate,
    inputPeak,
    outputPeak,

    // DSP Effects Enabled State
    isDspEffectsEnabled,
    isTogglingEnabled,

    // Advanced DSP State
    compressor,
    loudness,
    delay,

    // Multi-client support
    selectedTarget,
    availableTargets,
    linkedGroups,

    // Computed
    isAvailable,
    isConnected,
    isRunning,

    // Utils
    formatFrequency,
    FILTER_TYPES,

    // Actions
    initializeFilters,
    loadStatus,
    updateFilter,
    finalizeFilterUpdate,
    resetAllFilters,
    cleanup,

    // DSP Effects Enable/Disable
    loadEnabledState,
    toggleDspEffectsEnabled,

    // Target Management
    loadTargets,
    selectTarget,
    restoreClientSettings,

    // Linked Clients Management
    linkClients,
    unlinkClient,
    clearAllLinks,
    updateZoneName,
    isClientLinked,
    getLinkedClientIds,
    getZoneName,
    getZoneGroup,

    // Preset Management
    savePreset,
    loadPreset,
    deletePreset,

    // Advanced Features
    updateCompressor,
    updateLoudness,
    updateDelay,
    updateDspMute,

    // Unified client DSP state (for multiroom and DSP modal)
    clientDspState,
    currentTargetVolume,
    currentTargetMute,
    loadAllClientDspVolumes,
    updateClientDspVolume,
    setClientDspVolume,
    setClientDspMute,
    getClientDspVolume,
    getClientDspMute,
    clearClientDspVolumes,
    applyGlobalDelta,

    // WebSocket Handlers
    handleFilterChanged,
    handleFiltersReset,
    handleStateChanged,
    handlePresetLoaded,
    handleLevels,
    handleCompressorChanged,
    handleLoudnessChanged,
    handleDelayChanged,
    handleMuteChanged,
    handleLinksChanged,
    handleEnabledChanged,
    handleClientVolumesPushed
  };
});
