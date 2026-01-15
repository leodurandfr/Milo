// frontend/src/stores/clientRegistryStore.js
/**
 * Pinia store for centralized client/zone registry.
 *
 * This store is the single source of truth for:
 * - Client list with availability status
 * - Zone (linked group) configuration
 * - Client speaker types
 *
 * State is synchronized with backend via WebSocket events.
 * All mutations go through API calls - state updates come via WebSocket.
 */
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import axios from 'axios';

const CACHE_KEY = 'client_registry_cache';

export const useClientRegistryStore = defineStore('clientRegistry', () => {
  // === STATE ===

  // Clients indexed by dsp_id
  const clients = ref(new Map());

  // Zones indexed by zone_id
  const zones = ref(new Map());

  // Loading state
  const isLoading = ref(false);
  const isInitialized = ref(false);

  // === COMPUTED ===

  /**
   * All clients as an array, sorted with 'milo' first.
   */
  const clientList = computed(() => {
    const list = Array.from(clients.value.values());
    return list.sort((a, b) => {
      if (a.host === 'milo') return -1;
      if (b.host === 'milo') return 1;
      return (a.name || a.dsp_id).localeCompare(b.name || b.dsp_id);
    });
  });

  /**
   * Only available (connected) clients.
   */
  const availableClients = computed(() => {
    return clientList.value.filter(c => c.available);
  });

  /**
   * All available client dsp_ids.
   */
  const availableClientIds = computed(() => {
    return availableClients.value.map(c => c.dsp_id);
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
   */
  async function fetchState() {
    isLoading.value = true;
    try {
      const response = await axios.get('/api/registry/state');
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
   * Get a client by dsp_id.
   */
  function getClient(dspId) {
    return clients.value.get(dspId);
  }

  /**
   * Check if a client is available.
   */
  function isClientAvailable(dspId) {
    const client = clients.value.get(dspId);
    return client ? client.available : false;
  }

  /**
   * Get client display name.
   */
  function getClientName(dspId) {
    const client = clients.value.get(dspId);
    return client?.name || dspId;
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
  function getZoneForClient(dspId) {
    for (const zone of zones.value.values()) {
      if (zone.client_ids && zone.client_ids.includes(dspId)) {
        return zone;
      }
    }
    return null;
  }

  /**
   * Get all client dsp_ids in a zone.
   */
  function getZoneClientIds(zoneId) {
    const zone = zones.value.get(zoneId);
    return zone?.client_ids || [];
  }

  /**
   * Get only available client dsp_ids in a zone.
   */
  function getAvailableZoneClientIds(zoneId) {
    const zone = zones.value.get(zoneId);
    if (!zone) return [];
    return zone.client_ids.filter(id => isClientAvailable(id));
  }

  /**
   * Get linked client IDs for a given client (including itself).
   * If client is in a zone, returns all zone members.
   * If not in a zone, returns just the client itself.
   */
  function getLinkedClientIds(dspId) {
    const zone = getZoneForClient(dspId);
    if (zone) {
      return zone.client_ids;
    }
    return [dspId];
  }

  /**
   * Get available linked client IDs for a given client.
   * Same as getLinkedClientIds but filtered to available clients only.
   */
  function getAvailableLinkedClientIds(dspId) {
    const linkedIds = getLinkedClientIds(dspId);
    return linkedIds.filter(id => isClientAvailable(id));
  }

  /**
   * Check if zone has an available subwoofer.
   */
  function hasAvailableSubwoofer(zoneId) {
    const zone = zones.value.get(zoneId);
    if (!zone) return false;

    return zone.client_ids.some(id => {
      const client = clients.value.get(id);
      return client && client.available && client.speaker_type === 'subwoofer';
    });
  }

  // === WEBSOCKET EVENT HANDLERS ===

  /**
   * Handle registry events from WebSocket.
   * Called by websocket service when registry events are received.
   */
  function handleRegistryEvent(event) {
    const { type, data } = event;

    switch (type) {
      case 'client_registered':
      case 'client_updated':
        if (data.client) {
          // Strip runtime fields - volume/mute live in volumeState
          clients.value.set(data.dsp_id, stripRuntimeFields(data.client));
          saveCache();
        }
        break;

      case 'client_unregistered':
        clients.value.delete(data.dsp_id);
        saveCache();
        break;

      case 'availability_changed':
        if (clients.value.has(data.dsp_id)) {
          const client = clients.value.get(data.dsp_id);
          client.available = data.available;
          // Update with full client data if provided (strip runtime fields)
          if (data.client) {
            clients.value.set(data.dsp_id, stripRuntimeFields(data.client));
          }
          saveCache();
        }
        break;

      // Note: volume_changed is handled by unifiedAudioStore.handleVolumeEvent()
      // Volume/mute data lives in volumeState.clients, not here

      case 'speaker_type_changed':
        if (clients.value.has(data.dsp_id)) {
          const client = clients.value.get(data.dsp_id);
          client.speaker_type = data.speaker_type;
          if (data.crossover_frequency !== undefined) {
            client.crossover_frequency = data.crossover_frequency;
          }
          saveCache();
        }
        break;

      case 'zone_created':
        if (data.zone) {
          zones.value.set(data.zone_id, data.zone);
          saveCache();
        }
        break;

      case 'zone_deleted':
        zones.value.delete(data.zone_id);
        saveCache();
        break;

      case 'zone_updated':
        if (data.zone) {
          zones.value.set(data.zone_id, data.zone);
          saveCache();
        }
        break;

      case 'zone_client_added':
      case 'zone_client_removed':
        // Refresh the specific zone
        fetchZone(data.zone_id);
        break;

      default:
        console.log('Unknown registry event:', type, data);
    }
  }

  // === API ACTIONS ===

  /**
   * Fetch a specific zone from backend.
   */
  async function fetchZone(zoneId) {
    try {
      const response = await axios.get(`/api/registry/zones/${zoneId}`);
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
   */
  async function createZone(zoneId, name, clientIds = []) {
    try {
      const response = await axios.post('/api/registry/zones', {
        id: zoneId,
        name,
        client_ids: clientIds
      });
      // State update will come via WebSocket
      return response.data;
    } catch (error) {
      console.error('Error creating zone:', error);
      throw error;
    }
  }

  /**
   * Delete a zone.
   */
  async function deleteZone(zoneId) {
    try {
      await axios.delete(`/api/registry/zones/${zoneId}`);
      // State update will come via WebSocket
      return true;
    } catch (error) {
      console.error('Error deleting zone:', error);
      throw error;
    }
  }

  /**
   * Update zone properties.
   */
  async function updateZone(zoneId, updates) {
    try {
      const response = await axios.put(`/api/registry/zones/${zoneId}`, updates);
      // State update will come via WebSocket
      return response.data;
    } catch (error) {
      console.error('Error updating zone:', error);
      throw error;
    }
  }

  /**
   * Set zone clients (replace all).
   */
  async function setZoneClients(zoneId, clientIds) {
    try {
      const response = await axios.put(`/api/registry/zones/${zoneId}/clients`, {
        client_ids: clientIds
      });
      // State update will come via WebSocket
      return response.data;
    } catch (error) {
      console.error('Error setting zone clients:', error);
      throw error;
    }
  }

  /**
   * Add a client to a zone.
   */
  async function addClientToZone(zoneId, dspId) {
    try {
      await axios.post(`/api/registry/zones/${zoneId}/clients/${dspId}`);
      // State update will come via WebSocket
      return true;
    } catch (error) {
      console.error('Error adding client to zone:', error);
      throw error;
    }
  }

  /**
   * Remove a client from a zone.
   */
  async function removeClientFromZone(zoneId, dspId) {
    try {
      await axios.delete(`/api/registry/zones/${zoneId}/clients/${dspId}`);
      // State update will come via WebSocket
      return true;
    } catch (error) {
      console.error('Error removing client from zone:', error);
      throw error;
    }
  }

  /**
   * Update client speaker type.
   */
  async function updateClientType(dspId, speakerType, crossoverFrequency = null) {
    try {
      const payload = { speaker_type: speakerType };
      if (crossoverFrequency !== null) {
        payload.crossover_frequency = crossoverFrequency;
      }
      const response = await axios.put(`/api/registry/clients/${dspId}/type`, payload);
      // State update will come via WebSocket
      return response.data;
    } catch (error) {
      console.error('Error updating client type:', error);
      throw error;
    }
  }

  /**
   * Update client display name.
   * @param {string} snapcastId - Snapcast client ID (MAC address for local)
   * @param {string} name - New display name
   * @returns {Promise<boolean>} Success status
   */
  async function updateClientName(snapcastId, name) {
    const trimmedName = name?.trim();
    if (!trimmedName) return false;

    try {
      const response = await axios.post(`/api/routing/snapcast/client/${snapcastId}/name`, {
        name: trimmedName
      });
      // State update will come via WebSocket (registry.client_updated)
      return response.data.status === 'success';
    } catch (error) {
      console.error('Error updating client name:', error);
      return false;
    }
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
    availableClients,
    availableClientIds,
    zoneList,
    clientCount,
    zoneCount,

    // Initialization
    initialize,
    fetchState,

    // Client queries
    getClient,
    isClientAvailable,
    getClientName,

    // Zone queries
    getZone,
    getZoneForClient,
    getZoneClientIds,
    getAvailableZoneClientIds,
    getLinkedClientIds,
    getAvailableLinkedClientIds,
    hasAvailableSubwoofer,

    // WebSocket handler
    handleRegistryEvent,

    // API actions
    createZone,
    deleteZone,
    updateZone,
    setZoneClients,
    addClientToZone,
    removeClientFromZone,
    updateClientType,
    updateClientName,

    // Cache management
    clearCache
  };
});
