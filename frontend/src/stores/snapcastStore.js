// frontend/src/stores/snapcastStore.js
/**
 * Snapcast store for Snapcast server configuration.
 *
 * Client data is derived from multiroomStore (single source of truth).
 * This store only manages Snapcast-specific server settings.
 */
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import axios from 'axios';
import { useMultiroomStore } from './multiroomStore';
import { useUnifiedAudioStore } from './unifiedAudioStore';

const DISPLAY_CACHE_KEY = 'multiroom_display_cache';

// Volume conversion helpers
// Backend uses dB (-72 to 0), Snapcast UI uses percentage (0-100)
const MIN_DB = -72;
const MAX_DB = 0;

function dbToPercent(db) {
  // Clamp to valid range
  const clampedDb = Math.max(MIN_DB, Math.min(MAX_DB, db));
  // Linear conversion: -72dB = 0%, 0dB = 100%
  return Math.round(((clampedDb - MIN_DB) / (MAX_DB - MIN_DB)) * 100);
}

function percentToDb(percent) {
  // Clamp to valid range
  const clampedPercent = Math.max(0, Math.min(100, percent));
  // Linear conversion: 0% = -72dB, 100% = 0dB
  return MIN_DB + (clampedPercent / 100) * (MAX_DB - MIN_DB);
}

export const useSnapcastStore = defineStore('snapcast', () => {
  // === DERIVED STATE FROM MULTIROOM REGISTRY ===
  const registryStore = useMultiroomStore();
  const audioStore = useUnifiedAudioStore();

  // Clients derived from multiroomStore with Snapcast-compatible format
  // This replaces the old ref([]) and provides backward compatibility
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
        mac: client.mac_id, // MAC address for backwards compatibility
        online: client.online,
        // Convert dB to percentage for UI
        volume: dbToPercent(volumeState?.volume_db ?? -60),
        muted: volumeState?.mute ?? false,
        last_seen_age: 0 // Not tracked here, use registry if needed
      };
    });
  });

  // AbortController for cancelling ongoing requests
  let serverConfigAbortController = null;
  const isLoading = computed(() => !registryStore.isInitialized);

  // Server config with defaults (overwritten when loaded from backend)
  const DEFAULT_SERVER_CONFIG = {
    buffer: 1000,
    codec: 'flac',
    chunk_ms: 20,
    sampleformat: '48000:32:2'
  };
  const serverConfig = ref({ ...DEFAULT_SERVER_CONFIG });
  const originalServerConfig = ref({ ...DEFAULT_SERVER_CONFIG });
  const isApplyingServerConfig = ref(false);
  const isLoadingServerConfig = ref(false);

  // Memorization of the last known number of clients (for skeletons)
  const lastKnownClientCount = ref(3);

  // Memorization of display items structure (for zone-aware skeletons)
  // Each item: { type: 'zone' | 'client' }
  const lastKnownDisplayItems = ref([
    { type: 'client' },
    { type: 'client' },
    { type: 'client' }
  ]);

  // === COMPUTED ===
  // Note: sortedClients removed - clients is already sorted (derived from registryStore.clientList)
  // Components should use 'clients' directly

  const hasServerConfigChanges = computed(() => {
    return JSON.stringify(serverConfig.value) !== JSON.stringify(originalServerConfig.value);
  });

  // Note: Client cache management removed - clients are derived from multiroomStore
  // which has its own caching mechanism

  // === DISPLAY CACHE MANAGEMENT ===
  function loadDisplayCache() {
    try {
      const cached = localStorage.getItem(DISPLAY_CACHE_KEY);
      if (!cached) return null;
      return JSON.parse(cached);
    } catch (error) {
      console.warn('Error loading display cache:', error);
      return null;
    }
  }

  function saveDisplayCache(displayItems) {
    try {
      const items = displayItems.map(item => ({
        type: item.isZone ? 'zone' : 'client'
      }));
      localStorage.setItem(DISPLAY_CACHE_KEY, JSON.stringify(items));
      lastKnownDisplayItems.value = items;
    } catch (error) {
      console.error('Error saving display cache:', error);
    }
  }

  function preloadDisplayCache() {
    const cache = loadDisplayCache();
    if (cache && cache.length > 0) {
      lastKnownDisplayItems.value = cache;
    }
  }

  // === API CALLS ===
  // Note: fetchClients removed - clients are derived from multiroomStore

  async function fetchServerConfig(signal = null) {
    try {
      const response = await axios.get('/api/routing/snapcast/server-config', { signal });
      if (response.data.config) {
        const fileConfig = response.data.config.file_config?.parsed_config?.stream || {};
        const streamConfig = response.data.config.stream_config || {};

        return {
          buffer: parseInt(fileConfig.buffer || streamConfig.buffer_ms || '1000'),
          codec: fileConfig.codec || streamConfig.codec || 'flac',
          chunk_ms: parseInt(fileConfig.chunk_ms || streamConfig.chunk_ms) || 20,
          sampleformat: '48000:32:2'
        };
      }
      return null;
    } catch (error) {
      if (axios.isCancel(error) || error.name === 'CanceledError') {
        return null; // Request was cancelled
      }
      console.error('Error fetching server config:', error);
      return null;
    }
  }

  // === ACTIONS - CLIENTS ===

  // Note: Client loading functions removed - clients are derived from multiroomStore

  /**
   * Initialize client registry (backward compatibility)
   * Now delegates to multiroomStore.initialize()
   */
  async function loadClients() {
    if (!registryStore.isInitialized) {
      await registryStore.initialize();
    }
    // Update lastKnownClientCount for skeleton rendering
    lastKnownClientCount.value = clients.value.length || 3;
  }

  /**
   * Preload cache (backward compatibility)
   * Returns the number of clients
   */
  function preloadCache() {
    return clients.value.length || lastKnownClientCount.value;
  }

  /**
   * Clear cache (backward compatibility - no-op since no local cache)
   */
  function clearCache() {
    // No-op - clients are derived from multiroomStore which manages its own cache
  }

  // === ACTIONS - SERVER CONFIG ===
  async function loadServerConfig() {
    // Cancel previous request if it exists
    if (serverConfigAbortController) {
      serverConfigAbortController.abort();
    }
    serverConfigAbortController = new AbortController();
    const signal = serverConfigAbortController.signal;

    isLoadingServerConfig.value = true;
    try {
      const config = await fetchServerConfig(signal);
      if (config) {
        serverConfig.value = config;
        originalServerConfig.value = { ...config };
      }
    } finally {
      isLoadingServerConfig.value = false;
      serverConfigAbortController = null;
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
        originalServerConfig.value = { ...serverConfig.value };
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

  // Note: WebSocket handlers for client events removed
  // Client state is now derived from multiroomStore which handles all registry events

  // === CLEANUP ===
  function cancelPendingRequests() {
    if (serverConfigAbortController) {
      serverConfigAbortController.abort();
      serverConfigAbortController = null;
    }
  }

  return {
    // State (clients is computed from multiroomStore, already sorted: local first, then alphabetical)
    clients,
    isLoading,
    serverConfig,
    originalServerConfig,
    isApplyingServerConfig,
    isLoadingServerConfig,
    lastKnownClientCount,
    lastKnownDisplayItems,

    // Computed
    hasServerConfigChanges,

    // Actions - Clients (backward compatibility)
    preloadCache,
    loadClients,
    clearCache,

    // Actions - Display Cache
    preloadDisplayCache,
    saveDisplayCache,

    // Actions - Server Config
    loadServerConfig,
    applyServerConfig,
    updateServerConfig,
    selectCodec,
    applyPreset,

    // Volume conversion utilities (exported for components that need them)
    dbToPercent,
    percentToDb,

    // Cleanup
    cancelPendingRequests
  };
});
