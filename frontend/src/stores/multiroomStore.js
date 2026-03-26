// frontend/src/stores/multiroomStore.js
/**
 * Pinia store for multiroom client/zone management.
 *
 * This store is the single source of truth for:
 * - Client list with online/offline status
 * - Zone (linked group) configuration
 * - Client speaker types
 *
 * State is synchronized with backend via WebSocket events.
 * All mutations go through API calls - state updates come via WebSocket.
 */
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import axios from 'axios';
import { logger } from '@/services/logger';
import { apiCall } from '@/services/apiCall';

const CACHE_KEY = 'multiroom_cache';

export const useMultiroomStore = defineStore('multiroom', () => {
  // === STATE ===

  // Clients indexed by mac_id
  const clients = ref(new Map());

  // Zones indexed by zone_id
  const zones = ref(new Map());

  // Pending clients (not yet in Snapcast) indexed by mac_id
  const pendingClients = ref(new Map());

  // Clients currently being configured/rebooting (for UI state)
  const configuringClients = ref(new Set());
  const configuringTimeouts = {};
  const CONFIGURING_TIMEOUT_MS = 120000; // 2 minutes

  // Loading state
  const isLoading = ref(false);
  const isInitialized = ref(false);

  // Routing transition state (centralized for all components)
  // 'idle' | 'enabling' | 'disabling' | 'error'
  const transitionState = ref('idle');
  const transitionError = ref('');
  let transitionTimeoutId = null;
  const TRANSITION_TIMEOUT_MS = 15000;

  // === COMPUTED ===

  /**
   * Whether a routing transition is in progress (enabling or disabling).
   */
  const isTransitioning = computed(() =>
    transitionState.value === 'enabling' || transitionState.value === 'disabling'
  );

  /**
   * All clients as an array, with canonical sort order:
   * 1. Local client first (is_local === true)
   * 2. Online clients alphabetically
   * 3. Offline clients alphabetically
   */
  const clientList = computed(() => {
    const list = Array.from(clients.value.values());
    return list.sort((a, b) => {
      // Local client always first (using is_local property from backend)
      if (a.is_local) return -1;
      if (b.is_local) return 1;

      // Then by online status (online first)
      if (a.online && !b.online) return -1;
      if (!a.online && b.online) return 1;

      // Then alphabetically by name (fallback to mac_id)
      return (a.name || a.mac_id).localeCompare(b.name || b.mac_id, undefined, { sensitivity: 'base' });
    });
  });

  /**
   * Only online (connected) clients.
   */
  const onlineClients = computed(() => {
    return clientList.value.filter(c => c.online);
  });

  /**
   * All online client mac_ids.
   */
  const onlineClientIds = computed(() => {
    return onlineClients.value.map(c => c.mac_id);
  });

  /**
   * All zones as an array.
   */
  const zoneList = computed(() => {
    return Array.from(zones.value.values());
  });

  /**
   * Number of registered clients.
   */
  const clientCount = computed(() => clients.value.size);

  /**
   * Number of zones.
   */
  const zoneCount = computed(() => zones.value.size);

  /**
   * Pending clients as an array.
   */
  const pendingClientList = computed(() => Array.from(pendingClients.value.values()));

  // === CACHE MANAGEMENT ===

  function loadCache() {
    try {
      const cached = localStorage.getItem(CACHE_KEY);
      if (!cached) return null;
      return JSON.parse(cached);
    } catch (error) {
      logger.warn('store', 'Error loading registry cache', error);
      return null;
    }
  }

  function saveCache() {
    try {
      const data = {
        clients: Object.fromEntries(clients.value),
        zones: Object.fromEntries(zones.value),
        timestamp: Date.now()
      };
      localStorage.setItem(CACHE_KEY, JSON.stringify(data));
    } catch (error) {
      logger.error('store', 'Error saving registry cache', error);
    }
  }

  function clearCache() {
    try {
      localStorage.removeItem(CACHE_KEY);
    } catch (error) {
      logger.error('store', 'Error clearing registry cache', error);
    }
  }

  // === INITIALIZATION ===

  /**
   * Initialize store from cache, then fetch fresh state from backend.
   */
  async function initialize() {
    // Load from cache first for instant UI
    const cached = loadCache();
    if (cached && cached.clients) {
      clients.value = new Map(Object.entries(cached.clients));
      zones.value = new Map(Object.entries(cached.zones || {}));
    }

    // Fetch fresh state from backend
    await fetchState();
    isInitialized.value = true;
  }

  /**
   * Strip runtime fields from client data.
   * Volume/mute data lives in volumeState, not here.
   */
  function stripRuntimeFields(client) {
    const { volume_db, mute, ...metadata } = client;
    return metadata;
  }

  /**
   * Fetch complete state from backend.
   * Uses canonical /api/multiroom/state endpoint.
   */
  async function fetchState() {
    isLoading.value = true;
    try {
      const response = await axios.get('/api/multiroom/state');
      const { clients: clientsData, zones: zonesData } = response.data;

      // Update clients (strip runtime fields - volume/mute live in volumeState)
      const cleanedClients = new Map();
      for (const [id, client] of Object.entries(clientsData || {})) {
        cleanedClients.set(id, stripRuntimeFields(client));
      }
      clients.value = cleanedClients;

      // Update zones
      zones.value = new Map(Object.entries(zonesData || {}));

      // Save to cache
      saveCache();
    } catch (error) {
      logger.error('store', 'Error fetching registry state', error);
    } finally {
      isLoading.value = false;
    }
  }

  // === CLIENT QUERIES ===

  /**
   * Get a client by mac_id.
   */
  function getClient(macId) {
    return clients.value.get(macId);
  }

  /**
   * Check if a client is online.
   */
  function isClientOnline(macId) {
    const client = clients.value.get(macId);
    return client ? client.online : false;
  }

  /**
   * Get client display name.
   */
  function getClientName(macId) {
    const client = clients.value.get(macId);
    return client?.name || macId;
  }

  // === ZONE QUERIES ===

  /**
   * Get a zone by ID.
   */
  function getZone(zoneId) {
    return zones.value.get(zoneId);
  }

  /**
   * Get the zone a client belongs to.
   */
  function getZoneForClient(macId) {
    for (const zone of zones.value.values()) {
      if (zone.client_ids && zone.client_ids.includes(macId)) {
        return zone;
      }
    }
    return null;
  }

  /**
   * Get all client mac_ids in a zone.
   */
  function getZoneClientIds(zoneId) {
    const zone = zones.value.get(zoneId);
    return zone?.client_ids || [];
  }

  /**
   * Get only online client mac_ids in a zone.
   */
  function getOnlineZoneClientIds(zoneId) {
    const zone = zones.value.get(zoneId);
    if (!zone) return [];
    return zone.client_ids.filter(id => isClientOnline(id));
  }

  /**
   * Get linked client IDs for a given client (including itself).
   * If client is in a zone, returns all zone members.
   * If not in a zone, returns just the client itself.
   */
  function getLinkedClientIds(macId) {
    const zone = getZoneForClient(macId);
    if (zone) {
      return zone.client_ids;
    }
    return [macId];
  }

  /**
   * Get online linked client IDs for a given client.
   * Same as getLinkedClientIds but filtered to online clients only.
   */
  function getOnlineLinkedClientIds(macId) {
    const linkedIds = getLinkedClientIds(macId);
    return linkedIds.filter(id => isClientOnline(id));
  }

  /**
   * Check if zone has an online subwoofer.
   */
  function hasOnlineSubwoofer(zoneId) {
    const zone = zones.value.get(zoneId);
    if (!zone) return false;

    return zone.client_ids.some(id => {
      const client = clients.value.get(id);
      return client && client.online && client.speaker_type === 'subwoofer';
    });
  }

  // === WEBSOCKET EVENT HANDLERS ===

  /**
   * Handle multiroom category events from WebSocket.
   * This is the new standardized event format (Story 6.1/6.2).
   * @param {Object} event - WebSocket event with { type, data }
   *   - client_state_changed:
   *       Update/offline: { mac_id, client: { complete client object } }
   *       Deletion: { mac_id } (no client object)
   *   - zone_changed: { zone_id, zone: { enriched zone object } | null }
   */
  function handleMultiroomEvent(event) {
    const { type, data } = event;

    switch (type) {
      case 'client_state_changed':
        if (data.client && data.mac_id) {
          // Client updated or went offline — has complete client object
          const clientData = stripRuntimeFields(data.client);
          clients.value.set(data.mac_id, clientData);
          saveCache();
        } else if (data.mac_id && !data.client) {
          // Client deleted — no client object means removal from registry
          clients.value.delete(data.mac_id);
          saveCache();
        }
        break;

      case 'zone_changed':
        // Zone create/update/delete - null zone means deleted
        if (data.zone_id) {
          if (data.zone) {
            // Zone created or updated (enriched zone with computed fields)
            zones.value.set(data.zone_id, data.zone);
          } else {
            // Zone deleted (zone is null)
            zones.value.delete(data.zone_id);
          }
          saveCache();
        }
        break;

      case 'pending_client_changed':
        if (data.action === 'registered' || data.action === 'updated') {
          if (data.client?.mac_id) {
            const next = new Map(pendingClients.value);
            next.set(data.client.mac_id, data.client);
            pendingClients.value = next;
          }
        } else if (data.action === 'removed' && data.mac_id) {
          const nextPending = new Map(pendingClients.value);
          nextPending.delete(data.mac_id);
          pendingClients.value = nextPending;
          const nextConfiguring = new Set(configuringClients.value);
          nextConfiguring.delete(data.mac_id);
          configuringClients.value = nextConfiguring;
        }
        break;

      default:
        // Ignore unknown event types silently
        // Note: equalizer_changed and crossover_changed are handled by equalizerStore
        break;
    }
  }

  // === ROUTING TRANSITION HANDLERS ===

  function startTransitionTimeout() {
    clearTransitionTimeout();
    transitionTimeoutId = setTimeout(() => {
      if (transitionState.value === 'enabling' || transitionState.value === 'disabling') {
        logger.warn('store', 'Multiroom transition timeout reached');
        transitionState.value = 'error';
        transitionError.value = 'Transition timeout';
      }
    }, TRANSITION_TIMEOUT_MS);
  }

  function clearTransitionTimeout() {
    if (transitionTimeoutId) {
      clearTimeout(transitionTimeoutId);
      transitionTimeoutId = null;
    }
  }

  /**
   * Handle routing category events from WebSocket.
   * Centralized transition state for all multiroom UI components.
   * @param {Object} event - WebSocket event with { type, data }
   */
  function handleRoutingEvent(event) {
    switch (event.type) {
      case 'multiroom_enabling':
        transitionState.value = 'enabling';
        transitionError.value = '';
        startTransitionTimeout();
        break;

      case 'multiroom_disabling':
        transitionState.value = 'disabling';
        transitionError.value = '';
        startTransitionTimeout();
        break;

      case 'multiroom_ready':
        clearTransitionTimeout();
        transitionState.value = 'idle';
        break;

      case 'multiroom_error':
        clearTransitionTimeout();
        transitionState.value = 'error';
        transitionError.value = event.data?.message || '';
        break;
    }
  }

  /**
   * Reset transition state to idle.
   * Called when multiroom is fully deactivated (system state confirms).
   */
  function resetTransition() {
    clearTransitionTimeout();
    transitionState.value = 'idle';
    transitionError.value = '';
  }

  // === API ACTIONS ===

  /**
   * Create a new zone.
   * Uses canonical /api/multiroom/zones endpoint (Story 2-4).
   */
  async function createZone(name, clientIds = []) {
    return apiCall('store', 'Error creating zone', async () => {
      const response = await axios.post('/api/multiroom/zones', { name, client_ids: clientIds });
      const newZone = response.data.zone;
      if (newZone && newZone.id) {
        zones.value.set(newZone.id, newZone);
        saveCache();
      }
      return response.data;
    }, { rethrow: true });
  }

  /**
   * Delete a zone.
   * Uses canonical /api/multiroom/zones endpoint (Story 2-4).
   */
  async function deleteZone(zoneId) {
    return apiCall('store', 'Error deleting zone', async () => {
      await axios.delete(`/api/multiroom/zones/${zoneId}`);
      zones.value.delete(zoneId);
      saveCache();
      return true;
    }, { rethrow: true });
  }

  /**
   * Update zone properties (name).
   * Uses canonical /api/multiroom/zones endpoint (Story 2-4).
   */
  async function updateZone(zoneId, updates) {
    return apiCall('store', 'Error updating zone', async () => {
      const response = await axios.patch(`/api/multiroom/zones/${zoneId}`, updates);
      if (response.data.zone) {
        zones.value.set(zoneId, response.data.zone);
        saveCache();
      }
      return response.data;
    }, { rethrow: true });
  }

  /**
   * Add a client to a zone.
   * Client's equalizer is replaced by zone's shared equalizer (FR15).
   * @param {string} zoneId - Zone ID
   * @param {string} macId - Client mac_id to add
   * @returns {Promise<Object>} Response with updated zone data
   */
  async function addClientToZone(zoneId, macId) {
    return apiCall('store', 'Error adding client to zone', async () => {
      const response = await axios.post(`/api/multiroom/zones/${zoneId}/clients`, { mac_id: macId });
      if (response.data.zone) {
        zones.value.set(zoneId, response.data.zone);
        saveCache();
      }
      return response.data;
    }, { rethrow: true });
  }

  /**
   * Remove a client from a zone.
   * Client keeps zone equalizer as standalone equalizer (FR14).
   * If zone has < 2 clients after removal, zone is deleted.
   * @param {string} zoneId - Zone ID
   * @param {string} macId - Client mac_id to remove
   * @returns {Promise<Object>} Response with zone data or deletion message
   */
  async function removeClientFromZone(zoneId, macId) {
    return apiCall('store', 'Error removing client from zone', async () => {
      const response = await axios.delete(`/api/multiroom/zones/${zoneId}/clients/${macId}`);
      if (response.data.zone) {
        zones.value.set(zoneId, response.data.zone);
      } else if (response.data.message?.includes('deleted')) {
        zones.value.delete(zoneId);
      }
      saveCache();
      return response.data;
    }, { rethrow: true });
  }

  /**
   * Update client speaker type.
   * Uses canonical PATCH /api/multiroom/clients/{mac_id} endpoint.
   * @param {string} macId - Client mac_id
   * @param {string} speakerType - New speaker type
   * @returns {Promise<Object>} Updated client data
   */
  async function updateClientType(macId, speakerType) {
    return await updateClient(macId, { speaker_type: speakerType });
  }

  /**
   * Update client properties (name and/or speaker_type).
   * Uses canonical PATCH /api/multiroom/clients/{mac_id} endpoint.
   * @param {string} macId - Client mac_id
   * @param {Object} updates - { name?: string, speaker_type?: string }
   * @returns {Promise<Object>} Updated client data
   */
  async function updateClient(macId, updates) {
    return apiCall('store', 'Error updating client', async () => {
      const response = await axios.patch(`/api/multiroom/clients/${macId}`, updates);
      return response.data;
    }, { rethrow: true });
  }

  /**
   * Permanently delete a client from the registry.
   * Removes client from all zones and clears persisted configuration.
   * @param {string} macId - Client mac_id
   * @returns {Promise<boolean>} Success status
   */
  async function deleteClient(macId) {
    return apiCall('store', 'Error deleting client', async () => {
      const response = await axios.delete(`/api/multiroom/clients/${macId}`);
      return response.data.status === 'success';
    });
  }

  // === CLIENT HARDWARE ===

  /**
   * Fetch hardware configuration from a registered milo-client.
   * @param {string} macId - Client MAC address
   * @returns {Promise<Object>} Hardware config { audio: { id, overlay } }
   */
  async function fetchClientHardware(macId) {
    return apiCall('store', 'Error fetching client hardware', async () => {
      const response = await axios.get(`/api/multiroom/clients/${encodeURIComponent(macId)}/hardware`);
      return response.data;
    }, { rethrow: true });
  }

  /**
   * Change audio card on a registered milo-client and reboot it.
   * @param {string} macId - Client MAC address
   * @param {string} audioId - Audio card ID from hardware registry
   * @returns {Promise<Object>} Response with status
   */
  async function configureClientAudio(macId, audioId, volumeControl = null) {
    return apiCall('store', 'Error configuring client audio', async () => {
      const body = { audio_id: audioId };
      if (volumeControl !== null) body.volume_control = volumeControl;
      const response = await axios.put(
        `/api/multiroom/clients/${encodeURIComponent(macId)}/audio`,
        body,
      );
      return response.data;
    }, { rethrow: true });
  }

  // === PENDING CLIENTS ===

  /**
   * Fetch all pending clients from the backend.
   */
  async function fetchPendingClients() {
    return apiCall('store', 'Error fetching pending clients', async () => {
      const response = await axios.get('/api/multiroom/pending-clients');
      const data = response.data.clients || {};
      pendingClients.value = new Map(Object.entries(data));
    });
  }

  /**
   * Configure a pending client's audio and reboot it.
   * @param {string} macId - Client MAC address
   * @param {Object} config - { name, speaker_type, audio_id }
   */
  async function configurePendingClient(macId, config) {
    // Add to configuring set BEFORE the request to avoid race with WebSocket events
    configuringClients.value = new Set([...configuringClients.value, macId]);
    try {
      const result = await apiCall('store', 'Error configuring pending client', async () => {
        const response = await axios.post(
          `/api/multiroom/pending-clients/${encodeURIComponent(macId)}/configure`,
          config,
        );
        return response.data;
      }, { rethrow: true });

      // Auto-clear configuring state after timeout (cleanup if client never comes back)
      configuringTimeouts[macId] = setTimeout(() => {
        clearConfiguringClient(macId);
      }, CONFIGURING_TIMEOUT_MS);

      return result;
    } catch (e) {
      // Remove from configuring set on failure
      const next = new Set(configuringClients.value);
      next.delete(macId);
      configuringClients.value = next;
      throw e;
    }
  }

  /**
   * Check if a pending client is currently being configured/rebooting.
   */
  function isClientConfiguring(macId) {
    return configuringClients.value.has(macId);
  }

  /**
   * Remove a client from the configuring set and clear its timeout.
   */
  function clearConfiguringClient(macId) {
    if (configuringTimeouts[macId]) {
      clearTimeout(configuringTimeouts[macId]);
      delete configuringTimeouts[macId];
    }
    const next = new Set(configuringClients.value);
    next.delete(macId);
    configuringClients.value = next;
  }

  // === SYNC STATUS HELPERS ===

  /**
   * Check if a client has a sync error.
   * @param {string} macId - Client mac_id
   * @returns {boolean} True if client has sync error
   */
  function hasSyncError(macId) {
    const client = clients.value.get(macId);
    return client?.sync_status?.equalizer_synced === false || client?.sync_status?.volume_synced === false;
  }

  /**
   * Check if a client is currently syncing.
   * @param {string} macId - Client mac_id
   * @returns {boolean} True if client is syncing
   */
  function isSyncing(macId) {
    const client = clients.value.get(macId);
    return client?.sync_status?.syncing === true;
  }

  /**
   * Get sync status for a client.
   * @param {string} macId - Client mac_id
   * @returns {Object|null} Sync status { volume_synced, equalizer_synced, pending_applied }
   */
  function getSyncStatus(macId) {
    const client = clients.value.get(macId);
    return client?.sync_status || null;
  }

  // === RETURN PUBLIC API ===

  return {
    // State
    clients,
    zones,
    pendingClients,
    configuringClients,
    isLoading,
    isInitialized,
    transitionState,
    transitionError,

    // Computed
    clientList,
    onlineClients,
    onlineClientIds,
    zoneList,
    pendingClientList,
    clientCount,
    zoneCount,
    isTransitioning,

    // Initialization
    initialize,
    fetchState,

    // Client queries
    getClient,
    isClientOnline,
    getClientName,

    // Zone queries
    getZone,
    getZoneForClient,
    getZoneClientIds,
    getOnlineZoneClientIds,
    getLinkedClientIds,
    getOnlineLinkedClientIds,
    hasOnlineSubwoofer,

    // WebSocket handlers
    handleMultiroomEvent,
    handleRoutingEvent,
    resetTransition,

    // API actions
    createZone,
    deleteZone,
    updateZone,
    addClientToZone,
    removeClientFromZone,
    updateClientType,
    updateClient,
    deleteClient,

    // Client hardware
    fetchClientHardware,
    configureClientAudio,

    // Pending clients
    fetchPendingClients,
    configurePendingClient,
    isClientConfiguring,
    clearConfiguringClient,

    // Sync status
    hasSyncError,
    isSyncing,
    getSyncStatus,

    // Cache management
    clearCache
  };
});
