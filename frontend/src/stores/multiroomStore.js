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

const CACHE_KEY = 'multiroom_cache';

export const useMultiroomStore = defineStore('multiroom', () => {
  // === STATE ===

  // Clients indexed by mac_id
  const clients = ref(new Map());

  // Zones indexed by zone_id
  const zones = ref(new Map());

  // Loading state
  const isLoading = ref(false);
  const isInitialized = ref(false);

  // === COMPUTED ===

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

  // === CACHE MANAGEMENT ===

  function loadCache() {
    try {
      const cached = localStorage.getItem(CACHE_KEY);
      if (!cached) return null;
      return JSON.parse(cached);
    } catch (error) {
      console.warn('Error loading registry cache:', error);
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
      console.error('Error saving registry cache:', error);
    }
  }

  function clearCache() {
    try {
      localStorage.removeItem(CACHE_KEY);
    } catch (error) {
      console.error('Error clearing registry cache:', error);
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
      console.error('Error fetching registry state:', error);
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
   *   - client_state_changed: { mac_id, client: { complete client object } }
   *   - zone_changed: { zone_id, zone: { enriched zone object } | null }
   */
  function handleMultiroomEvent(event) {
    const { type, data } = event;

    switch (type) {
      case 'client_state_changed':
        // Complete client object in data.client
        if (data.client && data.mac_id) {
          const clientData = stripRuntimeFields(data.client);
          clients.value.set(data.mac_id, clientData);
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

      default:
        // Ignore unknown event types silently
        // Note: dsp_changed and crossover_changed are handled by dspStore
        break;
    }
  }

  // === API ACTIONS ===

  /**
   * Fetch a specific zone from backend.
   * Uses canonical /api/multiroom/zones endpoint.
   */
  async function fetchZone(zoneId) {
    try {
      const response = await axios.get(`/api/multiroom/zones/${zoneId}`);
      zones.value.set(zoneId, response.data);
      saveCache();
    } catch (error) {
      if (error.response?.status === 404) {
        zones.value.delete(zoneId);
        saveCache();
      } else {
        console.error(`Error fetching zone ${zoneId}:`, error);
      }
    }
  }

  /**
   * Create a new zone.
   * Uses canonical /api/multiroom/zones endpoint (Story 2-4).
   */
  async function createZone(name, clientIds = []) {
    try {
      const response = await axios.post('/api/multiroom/zones', {
        name,
        client_ids: clientIds
      });
      // Immediate local state update - don't wait for WebSocket
      const newZone = response.data.zone;
      if (newZone && newZone.id) {
        zones.value.set(newZone.id, newZone);
        saveCache();
      }
      return response.data;
    } catch (error) {
      console.error('Error creating zone:', error);
      throw error;
    }
  }

  /**
   * Delete a zone.
   * Uses canonical /api/multiroom/zones endpoint (Story 2-4).
   */
  async function deleteZone(zoneId) {
    try {
      await axios.delete(`/api/multiroom/zones/${zoneId}`);
      // Immediate local state update - don't wait for WebSocket
      zones.value.delete(zoneId);
      saveCache();
      return true;
    } catch (error) {
      console.error('Error deleting zone:', error);
      throw error;
    }
  }

  /**
   * Update zone properties (name).
   * Uses canonical /api/multiroom/zones endpoint (Story 2-4).
   */
  async function updateZone(zoneId, updates) {
    try {
      const response = await axios.patch(`/api/multiroom/zones/${zoneId}`, updates);
      // Immediate local state update - don't wait for WebSocket
      if (response.data.zone) {
        zones.value.set(zoneId, response.data.zone);
        saveCache();
      }
      return response.data;
    } catch (error) {
      console.error('Error updating zone:', error);
      throw error;
    }
  }

  /**
   * Add a client to a zone.
   * Client's DSP is replaced by zone's shared DSP (FR15).
   * @param {string} zoneId - Zone ID
   * @param {string} macId - Client mac_id to add
   * @returns {Promise<Object>} Response with updated zone data
   */
  async function addClientToZone(zoneId, macId) {
    try {
      const response = await axios.post(`/api/multiroom/zones/${zoneId}/clients`, {
        mac_id: macId
      });
      // Immediate local state update - don't wait for WebSocket
      if (response.data.zone) {
        zones.value.set(zoneId, response.data.zone);
        saveCache();
      }
      return response.data;
    } catch (error) {
      console.error('Error adding client to zone:', error);
      throw error;
    }
  }

  /**
   * Remove a client from a zone.
   * Client keeps zone DSP as standalone DSP (FR14).
   * If zone has < 2 clients after removal, zone is deleted.
   * @param {string} zoneId - Zone ID
   * @param {string} macId - Client mac_id to remove
   * @returns {Promise<Object>} Response with zone data or deletion message
   */
  async function removeClientFromZone(zoneId, macId) {
    try {
      const response = await axios.delete(`/api/multiroom/zones/${zoneId}/clients/${macId}`);
      // Immediate local state update - don't wait for WebSocket
      if (response.data.zone) {
        // Zone still exists with updated client list
        zones.value.set(zoneId, response.data.zone);
      } else if (response.data.message?.includes('deleted')) {
        // Zone was auto-deleted (< 2 clients remaining)
        zones.value.delete(zoneId);
      }
      saveCache();
      return response.data;
    } catch (error) {
      console.error('Error removing client from zone:', error);
      throw error;
    }
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
    try {
      const response = await axios.patch(`/api/multiroom/clients/${macId}`, updates);
      // State update will come via WebSocket (registry:client_updated)
      return response.data;
    } catch (error) {
      console.error('Error updating client:', error);
      throw error;
    }
  }

  /**
   * Permanently delete a client from the registry.
   * Removes client from all zones and clears persisted configuration.
   * @param {string} macId - Client mac_id
   * @returns {Promise<boolean>} Success status
   */
  async function deleteClient(macId) {
    try {
      const response = await axios.delete(`/api/multiroom/clients/${macId}`);
      // State update will come via WebSocket (client_disconnected)
      return response.data.status === 'success';
    } catch (error) {
      console.error('Error deleting client:', error);
      return false;
    }
  }

  // === SYNC STATUS HELPERS ===

  /**
   * Check if a client has a sync error.
   * @param {string} macId - Client mac_id
   * @returns {boolean} True if client has sync error
   */
  function hasSyncError(macId) {
    const client = clients.value.get(macId);
    return client?.sync_status?.dsp_synced === false || client?.sync_status?.volume_synced === false;
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
   * @returns {Object|null} Sync status { volume_synced, dsp_synced, pending_applied }
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
    isLoading,
    isInitialized,

    // Computed
    clientList,
    onlineClients,
    onlineClientIds,
    zoneList,
    clientCount,
    zoneCount,

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

    // API actions
    createZone,
    deleteZone,
    updateZone,
    addClientToZone,
    removeClientFromZone,
    updateClientType,
    updateClient,
    deleteClient,

    // Sync status
    hasSyncError,
    isSyncing,
    getSyncStatus,

    // Cache management
    clearCache
  };
});
