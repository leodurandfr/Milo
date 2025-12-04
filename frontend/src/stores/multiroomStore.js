// frontend/src/stores/multiroomStore.js
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import axios from 'axios';

const CACHE_KEY = 'multiroom_clients_cache';

export const useMultiroomStore = defineStore('multiroom', () => {
  // === STATE ===
  const clients = ref([]);
  const isLoading = ref(false);
  const serverConfig = ref({
    buffer: 1000,
    codec: 'flac',
    chunk_ms: 20,
    sampleformat: '48000:16:2'
  });
  const originalServerConfig = ref({});
  const isApplyingServerConfig = ref(false);
  const isLoadingServerConfig = ref(false);

  // Memorization of the last known number of clients (for skeletons)
  const lastKnownClientCount = ref(3);

  // === COMPUTED ===
  const sortedClients = computed(() => {
    const clientsList = [...clients.value];
    return clientsList.sort((a, b) => {
      if (a.host === 'milo') return -1;
      if (b.host === 'milo') return 1;
      return a.name.localeCompare(b.name);
    });
  });

  const hasServerConfigChanges = computed(() => {
    return JSON.stringify(serverConfig.value) !== JSON.stringify(originalServerConfig.value);
  });

  // === CACHE MANAGEMENT ===
  function loadCache() {
    try {
      const cached = localStorage.getItem(CACHE_KEY);
      return cached ? JSON.parse(cached) : null;
    } catch (error) {
      console.warn('Error loading multiroom cache (corrupted?):', error);
      return null;
    }
  }

  function saveCache(clientsList) {
    try {
      localStorage.setItem(CACHE_KEY, JSON.stringify(clientsList));
    } catch (error) {
      console.error('Error saving multiroom cache:', error);
    }
  }

  function clearCache() {
    try {
      localStorage.removeItem(CACHE_KEY);
    } catch (error) {
      console.error('Error clearing multiroom cache:', error);
    }
  }

  // === API CALLS ===
  async function fetchClients() {
    try {
      const response = await axios.get('/api/routing/snapcast/clients');
      return response.data.clients || [];
    } catch (error) {
      console.error('Error fetching multiroom clients:', error);
      return [];
    }
  }

  async function fetchServerConfig() {
    try {
      const response = await axios.get('/api/routing/snapcast/server-config');
      if (response.data.config) {
        const fileConfig = response.data.config.file_config?.parsed_config?.stream || {};
        const streamConfig = response.data.config.stream_config || {};

        return {
          buffer: parseInt(fileConfig.buffer || streamConfig.buffer_ms || '1000'),
          codec: fileConfig.codec || streamConfig.codec || 'flac',
          chunk_ms: parseInt(fileConfig.chunk_ms || streamConfig.chunk_ms) || 20,
          sampleformat: '48000:16:2'
        };
      }
      return null;
    } catch (error) {
      console.error('Error fetching server config:', error);
      return null;
    }
  }

  // === ACTIONS - CLIENTS ===

  /**
   * Preloads the cache synchronously and updates lastKnownClientCount
   * Returns the number of clients in the cache (or the default value)
   */
  function preloadCache() {
    const cache = loadCache();
    if (cache && cache.length > 0) {
      lastKnownClientCount.value = cache.length;
      clients.value = cache;
      return cache.length;
    }
    return lastKnownClientCount.value;
  }

  async function loadClients(forceNoCache = false) {
    // If clients are already loaded (via preloadCache), just refresh
    if (clients.value.length > 0 && !forceNoCache) {
      isLoading.value = false;

      // Refresh in the background
      const freshClients = await fetchClients();
      const sortedFresh = sortClients(freshClients);

      if (JSON.stringify(sortedFresh) !== JSON.stringify(clients.value)) {
        clients.value = sortedFresh;
        lastKnownClientCount.value = sortedFresh.length;
        saveCache(sortedFresh);
      }
      return;
    }

    // Otherwise, load normally with cache
    const cache = forceNoCache ? null : loadCache();

    if (cache && cache.length > 0 && !forceNoCache) {
      lastKnownClientCount.value = cache.length;
      clients.value = cache;
      isLoading.value = false;

      // Refresh in the background
      const freshClients = await fetchClients();
      const sortedFresh = sortClients(freshClients);

      if (JSON.stringify(sortedFresh) !== JSON.stringify(cache)) {
        clients.value = sortedFresh;
        saveCache(sortedFresh);
      }
    } else {
      // No cache: load with skeleton
      isLoading.value = true;
      const freshClients = await fetchClients();
      const sortedClients = sortClients(freshClients);

      lastKnownClientCount.value = sortedClients.length || 3;
      clients.value = sortedClients;
      saveCache(sortedClients);
      isLoading.value = false;
    }
  }

  async function updateClientVolume(clientId, volume) {
    try {
      await axios.post(`/api/routing/snapcast/client/${clientId}/volume`, { volume });
      return true;
    } catch (error) {
      console.error('Error updating client volume:', error);
      return false;
    }
  }

  async function toggleClientMute(clientId, muted) {
    try {
      await axios.post(`/api/routing/snapcast/client/${clientId}/mute`, { muted });
      return true;
    } catch (error) {
      console.error('Error toggling client mute:', error);
      return false;
    }
  }

  async function updateClientName(clientId, name) {
    const trimmedName = name?.trim();
    if (!trimmedName) return false;

    try {
      const response = await axios.post(`/api/routing/snapcast/client/${clientId}/name`, {
        name: trimmedName
      });
      return response.data.status === 'success';
    } catch (error) {
      console.error('Error updating client name:', error);
      return false;
    }
  }

  // === ACTIONS - SERVER CONFIG ===
  async function loadServerConfig() {
    isLoadingServerConfig.value = true;
    try {
      const config = await fetchServerConfig();
      if (config) {
        serverConfig.value = config;
        originalServerConfig.value = JSON.parse(JSON.stringify(config));
      }
    } finally {
      isLoadingServerConfig.value = false;
    }
  }

  async function applyServerConfig() {
    if (!hasServerConfigChanges.value || isApplyingServerConfig.value) return false;

    isApplyingServerConfig.value = true;
    try {
      const response = await axios.post('/api/routing/snapcast/server/config', {
        config: serverConfig.value
      });

      if (response.data.status === 'success') {
        originalServerConfig.value = JSON.parse(JSON.stringify(serverConfig.value));
        console.log('Multiroom server config applied successfully');
        return true;
      }
      return false;
    } catch (error) {
      console.error('Error applying multiroom server config:', error);
      return false;
    } finally {
      isApplyingServerConfig.value = false;
    }
  }

  function updateServerConfig(updates) {
    serverConfig.value = { ...serverConfig.value, ...updates };
  }

  function selectCodec(codecName) {
    serverConfig.value.codec = codecName;
  }

  function applyPreset(preset) {
    serverConfig.value.buffer = preset.config.buffer;
    serverConfig.value.codec = preset.config.codec;
    serverConfig.value.chunk_ms = preset.config.chunk_ms;
  }

  // === WEBSOCKET EVENT HANDLERS ===
  function handleClientConnected(event) {
    const { client_id, client_name, client_host, client_ip, volume, muted } = event.data;

    if (!client_id) return;
    if (clients.value.find(c => c.id === client_id)) return;

    const newClient = {
      id: client_id,
      name: client_name || client_host || 'Unknown',
      host: client_host || 'unknown',
      volume: volume || 0,
      muted: muted || false,
      ip: client_ip || 'Unknown'
    };

    clients.value = sortClients([...clients.value, newClient]);
    saveCache(clients.value);
  }

  function handleClientDisconnected(event) {
    const clientId = event.data.client_id;
    clients.value = clients.value.filter(c => c.id !== clientId);
    saveCache(clients.value);
  }

  function handleClientVolumeChanged(event) {
    const { client_id, volume, muted } = event.data;
    const client = clients.value.find(c => c.id === client_id);
    if (client) {
      client.volume = volume;
      if (muted !== undefined) client.muted = muted;
    }
  }

  function handleClientNameChanged(event) {
    const { client_id, name } = event.data;
    const client = clients.value.find(c => c.id === client_id);
    if (client) {
      client.name = name;
      clients.value = sortClients(clients.value);
    }
  }

  function handleClientMuteChanged(event) {
    const { client_id, muted, volume } = event.data;
    const client = clients.value.find(c => c.id === client_id);
    if (client) {
      client.muted = muted;
      if (volume !== undefined) client.volume = volume;
    }
  }

  // === HELPERS ===
  function sortClients(clientsList) {
    return [...clientsList].sort((a, b) => {
      if (a.host === 'milo') return -1;
      if (b.host === 'milo') return 1;
      return a.name.localeCompare(b.name);
    });
  }

  return {
    // State
    clients,
    isLoading,
    serverConfig,
    originalServerConfig,
    isApplyingServerConfig,
    isLoadingServerConfig,
    lastKnownClientCount,

    // Computed
    sortedClients,
    hasServerConfigChanges,

    // Actions - Clients
    preloadCache,
    loadClients,
    updateClientVolume,
    toggleClientMute,
    updateClientName,
    clearCache,

    // Actions - Server Config
    loadServerConfig,
    applyServerConfig,
    updateServerConfig,
    selectCodec,
    applyPreset,

    // WebSocket Handlers
    handleClientConnected,
    handleClientDisconnected,
    handleClientVolumeChanged,
    handleClientNameChanged,
    handleClientMuteChanged
  };
});
