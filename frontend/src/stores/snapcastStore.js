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
  // single source for config, codec list, and presets).
  const PLACEHOLDER_SERVER_CONFIG = {
    buffer: 1000,
    codec: 'flac',
    chunk_ms: 20,
    sampleformat: '48000:32:2',
    snapclient_buffer_time: 80
  };
  const serverConfig = ref({ ...PLACEHOLDER_SERVER_CONFIG });
  const originalServerConfig = ref({ ...PLACEHOLDER_SERVER_CONFIG });
  const isApplyingServerConfig = ref(false);

  // Backend-declared capabilities (codec whitelist + quality presets),
  // populated alongside the server config fetch.
  const capabilities = ref({ codecs: [], presets: [] });

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
  // Note: fetchClients removed - clients are derived from multiroomStore

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

    return {
      buffer_ms: parsed.data.stream_config.buffer_ms,
      codec: parsed.data.stream_config.codec,
      chunk_ms: parsed.data.stream_config.chunk_ms,
      sampleformat: parsed.data.stream_config.sampleformat,
      snapclient_buffer_time: parsed.data.snapclient_buffer_time,
    };
  }

  // === ACTIONS - CLIENTS ===

  // Note: Client loading functions removed - clients are derived from multiroomStore

  /**
   * Ensure the client registry is initialized (delegates to multiroomStore).
   */
  async function loadClients() {
    if (!registryStore.isInitialized) {
      await registryStore.initialize();
    }
    // Update lastKnownClientCount for skeleton rendering
    lastKnownClientCount.value = clients.value.length || 3;
  }

  /**
   * Returns the number of clients (last known count for skeleton rendering).
   */
  function preloadCache() {
    return clients.value.length || lastKnownClientCount.value;
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
      if (config) {
        serverConfig.value = config;
        originalServerConfig.value = { ...config };
      }
    });
    serverConfigAbortController = null;
  }

  async function applyServerConfig() {
    if (!hasServerConfigChanges.value || isApplyingServerConfig.value) return false;

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

  // Note: WebSocket handlers for client events removed
  // Client state is now derived from multiroomStore which handles all registry events

  return {
    // State (clients is computed from multiroomStore, already sorted: local first, then alphabetical)
    clients,
    isLoading,
    serverConfig,
    capabilities,
    isApplyingServerConfig,
    lastKnownDisplayItems,

    // Computed
    hasServerConfigChanges,

    // Actions - Clients
    preloadCache,
    loadClients,

    // Actions - Display Cache
    preloadDisplayCache,
    saveDisplayCache,

    // Actions - Server Config
    loadServerConfig,
    applyServerConfig,
    selectCodec,
    applyPreset,
  };
});
