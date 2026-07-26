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
import { logger } from '@/services/logger';
import { apiCall } from '@/services/apiCall';
import { i18n } from '@/services/i18n';

const CACHE_KEY = 'multiroom_cache';

// Backend multiroom_error reason codes → i18n keys. Resolved to a localized
// display string here so every consumer of `transitionError` renders the same
// text (WS-event handling and its presentation mapping live in the store).
const MULTIROOM_ERROR_KEYS = {
  enable_failed: 'multiroom.enableFailed',
  disable_failed: 'multiroom.disableFailed',
};

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
   * All zones as an array.
   */
  const zoneList = computed(() => {
    return Array.from(zones.value.values());
  });

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

  // === INITIALIZATION ===

  /**
   * Show the last known registry instantly, before any request lands.
   *
   * Boot-only: the fetch that follows is resync(), the same one a reconnect or
   * a tab return runs, so there is a single description of this store's server
   * state. Pending clients belong to it — App.vue classifies an incoming
   * `pending_client_changed` as new by testing `pendingClients`, a satellite
   * re-registers every 15s and each heartbeat rebroadcasts action="registered",
   * so an empty map made a long-known satellite look brand new.
   */
  function primeFromCache() {
    const cached = loadCache();
    if (cached && cached.clients) {
      clients.value = new Map(Object.entries(cached.clients));
      zones.value = new Map(Object.entries(cached.zones || {}));
    }
  }

  /**
   * Strip runtime fields from client data.
   * Volume/mute data lives in volumeState, not here.
   */
  function stripRuntimeFields(client) {
    const { volume_db: _, mute: __, ...metadata } = client;
    return metadata;
  }

  /**
   * Fetch complete state from backend.
   * Uses canonical /api/multiroom/state endpoint.
   */
  async function fetchState() {
    isLoading.value = true;
    const result = await apiCall.get('/api/multiroom/state', {
      category: 'multiroom',
      message: 'Error fetching registry state'
    });
    if (result.ok) {
      const { clients: clientsData, zones: zonesData } = result.data;

      const cleanedClients = new Map();
      for (const [id, client] of Object.entries(clientsData || {})) {
        cleanedClients.set(id, stripRuntimeFields(client));
      }
      clients.value = cleanedClients;

      zones.value = new Map(Object.entries(zonesData || {}));

      saveCache();
    }
    isLoading.value = false;
    // Set even on failure: consumers read it as "the first fetch has been
    // attempted", to decide whether they must trigger one themselves.
    isInitialized.value = true;
  }

  // === CLIENT QUERIES ===

  /**
   * Check if a client is online.
   */
  function isClientOnline(macId) {
    const client = clients.value.get(macId);
    return client ? client.online : false;
  }

  // === ZONE QUERIES ===

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
        // Note: equalizer_changed is handled by equalizerStore
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

      case 'multiroom_error': {
        clearTransitionTimeout();
        transitionState.value = 'error';
        // Backend sends a stable reason code (enable_failed / disable_failed);
        // resolve it to a localized message for all consumers.
        const errorKey = MULTIROOM_ERROR_KEYS[event.data?.reason];
        transitionError.value = errorKey ? i18n.t(errorKey) : '';
        break;
      }
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
   * Uses the canonical /api/multiroom/zones endpoint.
   */
  async function createZone(name, clientIds = []) {
    const result = await apiCall.post('/api/multiroom/zones', { name, client_ids: clientIds }, {
      category: 'store',
      message: 'Error creating zone',
      rethrow: true,
    });
    const newZone = result.data.zone;
    if (newZone && newZone.id) {
      zones.value.set(newZone.id, newZone);
      saveCache();
    }
    return result.data;
  }

  /**
   * Delete a zone.
   * Uses the canonical /api/multiroom/zones endpoint.
   */
  async function deleteZone(zoneId) {
    await apiCall.delete(`/api/multiroom/zones/${zoneId}`, {
      category: 'store',
      message: 'Error deleting zone',
      rethrow: true,
    });
    zones.value.delete(zoneId);
    saveCache();
    return true;
  }

  /**
   * Update zone properties (name).
   * Uses the canonical /api/multiroom/zones endpoint.
   */
  async function updateZone(zoneId, updates) {
    const result = await apiCall.patch(`/api/multiroom/zones/${zoneId}`, updates, {
      category: 'store',
      message: 'Error updating zone',
      rethrow: true,
    });
    if (result.data.zone) {
      zones.value.set(zoneId, result.data.zone);
      saveCache();
    }
    return result.data;
  }

  /**
   * Add a client to a zone.
   * Client's equalizer is replaced by the zone's shared equalizer.
   * @param {string} zoneId - Zone ID
   * @param {string} macId - Client mac_id to add
   * @returns {Promise<Object>} Response with updated zone data
   */
  async function addClientToZone(zoneId, macId) {
    const result = await apiCall.post(`/api/multiroom/zones/${zoneId}/clients`, { mac_id: macId }, {
      category: 'store',
      message: 'Error adding client to zone',
      rethrow: true,
    });
    if (result.data.zone) {
      zones.value.set(zoneId, result.data.zone);
      saveCache();
    }
    return result.data;
  }

  /**
   * Remove a client from a zone.
   * Client keeps the zone equalizer as its standalone equalizer.
   * If zone has < 2 clients after removal, zone is deleted.
   * @param {string} zoneId - Zone ID
   * @param {string} macId - Client mac_id to remove
   * @returns {Promise<Object>} Response with zone data or deletion message
   */
  async function removeClientFromZone(zoneId, macId) {
    const result = await apiCall.delete(`/api/multiroom/zones/${zoneId}/clients/${macId}`, {
      category: 'store',
      message: 'Error removing client from zone',
      rethrow: true,
    });
    if (result.data.zone) {
      zones.value.set(zoneId, result.data.zone);
    } else if (result.data.message?.includes('deleted')) {
      zones.value.delete(zoneId);
    }
    saveCache();
    return result.data;
  }

  /**
   * Update client properties (name and/or speaker_type).
   * Uses canonical PATCH /api/multiroom/clients/{mac_id} endpoint.
   * @param {string} macId - Client mac_id
   * @param {Object} updates - { name?: string, speaker_type?: string }
   * @returns {Promise<Object>} Updated client data
   */
  async function updateClient(macId, updates) {
    const result = await apiCall.patch(`/api/multiroom/clients/${macId}`, updates, {
      category: 'store',
      message: 'Error updating client',
      rethrow: true,
    });
    return result.data;
  }

  /**
   * Permanently delete a client from the registry.
   * Removes client from all zones and clears persisted configuration.
   * @param {string} macId - Client mac_id
   * @returns {Promise<boolean>} Success status
   */
  async function deleteClient(macId) {
    const result = await apiCall.delete(`/api/multiroom/clients/${macId}`, {
      category: 'store',
      message: 'Error deleting client',
      checkStatus: true,
    });
    return result.ok;
  }

  // === CLIENT HARDWARE ===

  /**
   * Fetch hardware configuration from a registered milo-client.
   * @param {string} macId - Client MAC address
   * @returns {Promise<Object>} Hardware config { audio: { id, overlay } }
   */
  async function fetchClientHardware(macId) {
    const result = await apiCall.get(`/api/multiroom/clients/${encodeURIComponent(macId)}/hardware`, {
      category: 'store',
      message: 'Error fetching client hardware',
      rethrow: true,
    });
    return result.data;
  }

  /**
   * Change audio card on a registered milo-client and reboot it.
   * @param {string} macId - Client MAC address
   * @param {string} audioId - Audio card ID from hardware registry
   * @returns {Promise<Object>} Response with status
   */
  async function configureClientAudio(macId, audioId, volumeControl = null) {
    const body = { audio_id: audioId };
    if (volumeControl !== null) body.volume_control = volumeControl;
    const result = await apiCall.put(
      `/api/multiroom/clients/${encodeURIComponent(macId)}/audio`,
      body,
      {
        category: 'store',
        message: 'Error configuring client audio',
        rethrow: true,
      },
    );
    return result.data;
  }

  // === PENDING CLIENTS ===

  /**
   * Fetch all pending clients from the backend.
   */
  async function fetchPendingClients() {
    const result = await apiCall.get('/api/multiroom/pending-clients', {
      category: 'store',
      message: 'Error fetching pending clients',
    });
    if (result.ok) {
      const data = result.data.clients || {};
      pendingClients.value = new Map(Object.entries(data));
    }
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
      const result = await apiCall.post(
        `/api/multiroom/pending-clients/${encodeURIComponent(macId)}/configure`,
        config,
        {
          category: 'store',
          message: 'Error configuring pending client',
          rethrow: true,
        },
      );

      // Auto-clear configuring state after timeout (cleanup if client never comes back)
      configuringTimeouts[macId] = setTimeout(() => {
        clearConfiguringClient(macId);
      }, CONFIGURING_TIMEOUT_MS);

      return result.data;
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

  // === RETURN PUBLIC API ===

  async function resync() {
    await Promise.all([fetchState(), fetchPendingClients()]);
  }

  return {
    resync,
    // State
    clients,
    pendingClients,
    isLoading,
    isInitialized,
    transitionState,
    transitionError,

    // Computed
    clientList,
    zoneList,
    pendingClientList,
    isTransitioning,

    // Initialization
    primeFromCache,
    fetchState,

    // Client queries
    isClientOnline,

    // Zone queries
    getZoneForClient,
    getLinkedClientIds,
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
    updateClient,
    deleteClient,

    // Client hardware
    fetchClientHardware,
    configureClientAudio,

    // Pending clients
    fetchPendingClients,
    configurePendingClient,
    isClientConfiguring,
  };
});
