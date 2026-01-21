<!-- frontend/src/components/multiroom/MultiroomControl.vue -->
<template>
  <div class="clients-container">
    <div class="clients-list">
      <!-- Single Transition for both states -->
      <Transition name="fade-slide" mode="out-in">
        <!-- MESSAGE: Multiroom disabled or error -->
        <MessageContent v-if="showMessage" key="message" :icon="messageIcon" :title="messageTitle" />

        <!-- CLIENTS: Skeletons OR real items -->
        <div v-else key="clients" class="clients-wrapper">
          <MultiroomItem
            v-for="client in displayClients"
            :key="client.mac_id || client.id"
            :client="client"
            :is-loading="shouldShowLoading"
            :zone-clients="getZoneClients(client)"
            :is-zone="client.isZone || false"
            :zone-client-details="client.zoneClientDetails || null"
            @volume-change="handleVolumeChange"
            @mute-toggle="handleMuteToggle"
            @client-volume-change="handleClientVolumeChange"
            @client-mute-toggle="handleClientMuteToggle"
          />
        </div>
      </Transition>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue';
import { useI18n } from '@/services/i18n';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useMultiroomStore } from '@/stores/multiroomStore';
import { useDspStore } from '@/stores/dspStore';
import { useSettingsStore } from '@/stores/settingsStore';
import useWebSocket from '@/services/websocket';
import MultiroomItem from './MultiroomItem.vue';
import MessageContent from '@/components/ui/MessageContent.vue';

const { t } = useI18n();
const unifiedStore = useUnifiedAudioStore();
const multiroomStore = useMultiroomStore();
const dspStore = useDspStore();
const settingsStore = useSettingsStore();
const { on } = useWebSocket();

// Single transition state instead of 3 separate flags
// 'idle' | 'enabling' | 'disabling' | 'error'
const transitionState = ref('idle');
const errorMessage = ref('');

// Timeout for transition (15 seconds)
const TRANSITION_TIMEOUT_MS = 15000;
let transitionTimeoutId = null;

let unsubscribeFunctions = [];

// === COMPUTED ===
const isMultiroomActive = computed(() => unifiedStore.systemState.multiroom_enabled);

// Get linked groups from DSP store (zones are a multiroom feature, independent of DSP effects)
const linkedGroups = computed(() => dspStore.linkedGroups || []);

// Get zone info for a client (returns zone object if linked, null otherwise)
function getZoneForClient(client) {
  const macId = client.mac_id;
  if (!macId) return null;
  for (const group of linkedGroups.value) {
    if (group.client_ids?.includes(macId)) {
      return group;
    }
  }
  return null;
}

// Check if a client is the "primary" of its zone (first online in the list)
function isZonePrimary(client) {
  const zone = getZoneForClient(client);
  if (!zone) return true; // Not in a zone, show it

  // Find the first online client in the zone
  const firstOnlineId = zone.client_ids.find(macId =>
    multiroomStore.clients.some(c => c.mac_id === macId)
  );

  // This client is primary if it's the first online one
  return firstOnlineId === client.mac_id;
}

// Get zone average volume from unified volume state
function getZoneAverageVolume(zone) {
  if (!zone?.id) return -60;
  // Use pre-calculated zone volume from unified state
  const zoneData = unifiedStore.volumeState.zones[zone.id];
  if (zoneData && typeof zoneData.average_volume_db === 'number') {
    return zoneData.average_volume_db;
  }
  // Fallback: calculate from individual clients (only online clients)
  if (!zone?.client_ids?.length) return -60;
  // Filter to only connected clients
  const onlineClientIds = zone.client_ids.filter(macId =>
    multiroomStore.clients.some(c => c.mac_id === macId && c.online)
  );
  if (onlineClientIds.length === 0) return -60;

  const volumes = onlineClientIds.map(macId => dspStore.getClientDspVolume(macId));
  return volumes.reduce((sum, v) => sum + v, 0) / volumes.length;
}

// Check if a zone is muted from unified volume state
function getZoneMuted(zone) {
  if (!zone?.id) return false;
  // Use pre-calculated zone mute from unified state
  const zoneData = unifiedStore.volumeState.zones[zone.id];
  if (zoneData && typeof zoneData.all_muted === 'boolean') {
    return zoneData.all_muted;
  }
  // Fallback: check individual clients
  if (!zone?.client_ids?.length) return false;
  return zone.client_ids.every(macId => dspStore.getClientDspMute(macId));
}

// Track starting state when zone slider drag begins
// Structure: { zoneId: { startAvg, clientStarts: { macId: volume } } }
const zoneSliderState = ref({});

// Get or initialize zone slider state (called on first slider input)
function getZoneSliderState(zone) {
  const zoneId = zone.id || zone.client_ids.join('-');
  if (!zoneSliderState.value[zoneId]) {
    // Filter to only online clients (matching getZoneAverageVolume pattern)
    const onlineClientIds = zone.client_ids.filter(macId =>
      multiroomStore.clients.some(c => c.mac_id === macId && c.online)
    );

    // Handle edge case: no online clients
    if (onlineClientIds.length === 0) {
      zoneSliderState.value[zoneId] = { startAvg: -30, clientStarts: {} };
      return zoneSliderState.value[zoneId];
    }

    // Capture starting volumes for online clients only
    const clientStarts = {};
    onlineClientIds.forEach(macId => {
      clientStarts[macId] = dspStore.getClientDspVolume(macId);
    });
    const startAvg = Object.values(clientStarts).reduce((s, v) => s + v, 0) / onlineClientIds.length;
    zoneSliderState.value[zoneId] = { startAvg, clientStarts };
  }
  return zoneSliderState.value[zoneId];
}

// Clear zone slider state after drag ends
function clearZoneSliderState(zone) {
  const zoneId = zone.id || zone.client_ids.join('-');
  delete zoneSliderState.value[zoneId];
}

// Get zone clients for display (shows client names)
function getZoneClients(client) {
  // If client has zoneClients property (set by displayClients), use it
  if (client.zoneClients) {
    return client.zoneClients;
  }
  return '';
}

const showMessage = computed(() => {
  // Show message when:
  // - Error state
  // - Disabling (show "disabled" message immediately)
  // - Multiroom is off and not enabling
  if (transitionState.value === 'error') {
    return true;
  }
  if (transitionState.value === 'disabling') {
    return true;
  }
  if (transitionState.value === 'enabling') {
    return false;
  }
  return !isMultiroomActive.value;
});

const messageIcon = computed(() => {
  return transitionState.value === 'error' ? 'error' : 'multiroom';
});

const messageTitle = computed(() => {
  if (transitionState.value === 'error') {
    return errorMessage.value || t('multiroom.error');
  }
  return t('multiroom.disabled');
});

// Show loading skeletons during enabling or store loading
const shouldShowLoading = computed(() => {
  return transitionState.value === 'enabling' || multiroomStore.isLoading;
});

const displayClients = computed(() => {
  // Force Vue to track volumeState.zones and volumeState.clients as dependencies
  // This ensures recomputation when zone averages or client volumes change
  // eslint-disable-next-line no-unused-vars
  const _zones = unifiedStore.volumeState.zones;
  // eslint-disable-next-line no-unused-vars
  const _clients = unifiedStore.volumeState.clients;

  // During enabling or loading, show placeholders based on last known display structure
  if (transitionState.value === 'enabling' || (multiroomStore.clients.length === 0 && multiroomStore.isLoading)) {
    return multiroomStore.lastKnownDisplayItems.map((item, i) => ({
      id: `placeholder-${i}`,
      name: '',
      volume: 0,
      dspMuted: false,
      isZone: item.type === 'zone',
      zoneClientDetails: null
    }));
  }

  // Add dspVolume and dspMuted from cache to each client
  // If there are linked groups, filter to show only zone primaries
  if (linkedGroups.value.length > 0) {
    return multiroomStore.clients
      .filter(client => isZonePrimary(client))
      .map(client => {
        const dspVol = dspStore.getClientDspVolume(client.mac_id);
        const dspMut = dspStore.getClientDspMute(client.mac_id);
        const zone = getZoneForClient(client);

        if (zone) {
          // This is a zone primary - use custom name or fallback to "Zone X"
          const zoneIndex = linkedGroups.value.indexOf(zone) + 1;
          const zoneName = zone.name || `Zone ${zoneIndex}`;
          const sortedClientIds = dspStore.sortClientIdsLocalFirst(zone.client_ids);
          const clientNames = sortedClientIds
            .map(macId => {
              // Find client by mac_id
              const c = multiroomStore.clients.find(cl => cl.mac_id === macId);
              return c ? c.name : macId;
            })
            .join(' · ');

          // Build detailed client list for expanded view
          const zoneClientDetails = sortedClientIds
            .map(macId => {
              const c = multiroomStore.clients.find(cl => cl.mac_id === macId);

              // Skip clients not in the client list (offline clients already filtered by backend)
              if (!c) return null;

              return {
                id: macId,  // Use macId directly (c.id may be unreliable)
                mac_id: macId,
                name: c.name,
                dspVolume: dspStore.getClientDspVolume(macId),
                dspMuted: dspStore.getClientDspMute(macId),
                speakerType: dspStore.getClientSpeakerType(macId),
                online: c.online,
                is_local: c.is_local
              };
            })
            .filter(Boolean)
            .sort((a, b) => {
              // Local first (using is_local from backend, not hardcoded string)
              if (a.is_local && !b.is_local) return -1;
              if (!a.is_local && b.is_local) return 1;
              // Online clients first
              if (a.online && !b.online) return -1;
              if (!a.online && b.online) return 1;
              // Then alphabetically
              return a.name.localeCompare(b.name, undefined, { sensitivity: 'base' });
            });

          // Use arithmetic average of all clients in zone
          const zoneVolume = getZoneAverageVolume(zone);
          return {
            ...client,
            name: zoneName,
            zoneClients: clientNames,
            dspVolume: zoneVolume,
            dspMuted: getZoneMuted(zone),
            volumeLoading: zoneVolume === null,
            zoneClientIds: zone.client_ids,
            isZone: true,
            zoneClientDetails
          };
        }
        return {
          ...client,
          dspVolume: dspVol,
          dspMuted: dspMut,
          isZone: false,
          zoneClientDetails: null
        };
      })
      .sort((a, b) => {
        // Zone online status: zone is online if ANY client is online
        const aOnline = a.isZone
          ? a.zoneClientDetails?.some(c => c.online) ?? false
          : a.online;
        const bOnline = b.isZone
          ? b.zoneClientDetails?.some(c => c.online) ?? false
          : b.online;

        // Online items first
        if (aOnline && !bOnline) return -1;
        if (!aOnline && bOnline) return 1;

        // Within same availability: zones before individual clients
        if (a.isZone && !b.isZone) return -1;
        if (!a.isZone && b.isZone) return 1;

        // Alphabetically (already sorted by store, but needed after zone grouping)
        return a.name.localeCompare(b.name, undefined, { sensitivity: 'base' });
      });
  }

  // No linked groups - just add dspVolume and dspMuted to each client
  // Sorting handled by clientRegistryStore (local first, online first, alphabetical)
  return multiroomStore.clients.map(client => {
    const dspVol = dspStore.getClientDspVolume(client.mac_id);
    const dspMut = dspStore.getClientDspMute(client.mac_id);
    return {
      ...client,
      dspVolume: dspVol,
      dspMuted: dspMut,
      isZone: false,
      zoneClientDetails: null
    };
  });
});

// === HANDLERS ===
async function handleVolumeChange(clientMacId, volumeDb, options = {}) {
  const { isZone = false } = options;

  // Find client by mac_id (unique identifier for all clients)
  const client = multiroomStore.clients.find(c => c.mac_id === clientMacId);
  if (!client) return;

  // Use explicit isZone flag instead of recalculating zone membership
  if (isZone) {
    const zone = getZoneForClient(client);
    if (zone && zone.client_ids.length > 1) {
      // Zone volume change: apply DELTA atomically to entire zone
      // Get starting state (captures volumes at start of slider drag)
      const state = getZoneSliderState(zone);
      const delta = volumeDb - state.startAvg;

      // Single atomic API call for entire zone
      // This eliminates race condition - updates all clients in parallel, broadcasts once
      try {
        await dspStore.applyZoneDelta(zone.id, delta);
        // Volume state updated via single WebSocket broadcast from backend
      } catch (error) {
        console.error('Failed to apply zone volume delta:', error);
      }

      // Clear state after change completes (slider drag ended)
      clearZoneSliderState(zone);
    }
  } else {
    // Standalone client - always use direct update
    await dspStore.updateClientDspVolume(client.mac_id, volumeDb);
    // Volume state will be updated via WebSocket broadcast
  }
}

async function handleMuteToggle(clientMacId, muted, options = {}) {
  const { isZone = false } = options;

  // Find client by mac_id (unique identifier for all clients)
  const client = multiroomStore.clients.find(c => c.mac_id === clientMacId);
  if (!client) return;

  // Use explicit isZone flag instead of recalculating zone membership
  if (isZone) {
    const zone = getZoneForClient(client);
    if (zone && zone.client_ids.length > 1) {
      // Zone mute: mute ALL ONLINE clients in the zone
      const onlineClientIds = zone.client_ids.filter(macId =>
        multiroomStore.clients.some(c => c.mac_id === macId)
      );

      const updatePromises = onlineClientIds.map(async (macId) => {
        await dspStore.updateClientDspMute(macId, muted);
      });
      await Promise.all(updatePromises);
    }
  } else {
    // Standalone client - always use direct update
    await dspStore.updateClientDspMute(client.mac_id, muted);
  }
}

// Handle individual client volume change (within expanded zone)
async function handleClientVolumeChange(clientDspId, volumeDb) {
  await dspStore.updateClientDspVolume(clientDspId, volumeDb);
}

// Handle individual client mute toggle (within expanded zone)
async function handleClientMuteToggle(clientDspId, muted) {
  await dspStore.updateClientDspMute(clientDspId, muted);
}

// === TRANSITION HELPERS ===
function startTransitionTimeout() {
  clearTransitionTimeout();
  transitionTimeoutId = setTimeout(() => {
    if (transitionState.value === 'enabling' || transitionState.value === 'disabling') {
      console.warn('[MultiroomControl] Transition timeout reached');
      transitionState.value = 'error';
      errorMessage.value = t('multiroom.timeout_error');
      // Note: isLoading is now computed, no need to set it manually
    }
  }, TRANSITION_TIMEOUT_MS);
}

function clearTransitionTimeout() {
  if (transitionTimeoutId) {
    clearTimeout(transitionTimeoutId);
    transitionTimeoutId = null;
  }
}

// === WEBSOCKET HANDLERS ===
// Note: Client event handlers removed - clients are now derived from clientRegistryStore
// which handles registry events in App.vue. The snapcast events are no longer needed here.

function handleSystemStateChanged(event) {
  unifiedStore.updateState(event);
}

function handleMultiroomEnabling() {
  transitionState.value = 'enabling';
  errorMessage.value = '';
  // Note: isLoading is now computed from registryStore.isInitialized
  startTransitionTimeout();
}

function handleMultiroomDisabling() {
  transitionState.value = 'disabling';
  errorMessage.value = '';
  // Note: isLoading is now computed from registryStore.isInitialized
  startTransitionTimeout();
}

async function handleMultiroomReady() {
  clearTransitionTimeout();

  // Load clients now that services are ready
  await multiroomStore.loadClients(true); // forceNoCache=true
  // Volume data comes from unifiedAudioStore.volumeState via WebSocket

  transitionState.value = 'idle';
}

function handleMultiroomError(event) {
  console.error('[MultiroomControl] Received multiroom_error event:', event);
  clearTransitionTimeout();
  transitionState.value = 'error';
  errorMessage.value = event?.message || t('multiroom.error');
  // Note: isLoading is now computed from registryStore.isInitialized
}

// === LIFECYCLE ===
onMounted(async () => {
  // Preload display cache for zone-aware skeletons
  multiroomStore.preloadDisplayCache();

  // Reset transition state on mount based on current state
  if (isMultiroomActive.value) {
    transitionState.value = 'idle';
    // Preload cache synchronously to get the correct number of clients
    multiroomStore.preloadCache();
    // Load fresh clients in the background
    await multiroomStore.loadClients();
  } else {
    transitionState.value = 'idle';
  }

  // Load DSP enabled state (for volume mode detection)
  await dspStore.loadEnabledState();

  // Load linked groups (zones are a multiroom feature, independent of DSP effects)
  await dspStore.loadTargets();

  // Note: snapcast client event subscriptions removed - clients are now derived from
  // clientRegistryStore which handles registry events globally in App.vue
  unsubscribeFunctions.push(
    on('system', 'state_changed', handleSystemStateChanged),
    on('routing', 'multiroom_enabling', handleMultiroomEnabling),
    on('routing', 'multiroom_disabling', handleMultiroomDisabling),
    on('routing', 'multiroom_ready', handleMultiroomReady),
    on('routing', 'multiroom_error', handleMultiroomError),
    on('dsp', 'enabled_changed', (e) => dspStore.handleEnabledChanged(e)),
    // Volume changes - handled by unifiedAudioStore.handleVolumeEvent
    // The unified state update will trigger reactivity
    on('volume', 'volume_changed', (event) => {
      unifiedStore.handleVolumeEvent(event);
    })
  );
});

onUnmounted(() => {
  unsubscribeFunctions.forEach(unsubscribe => unsubscribe());
  clearTransitionTimeout();
});

// === WATCHERS ===
// Watch for deactivation completion (when state becomes false)
watch(isMultiroomActive, (newValue, oldValue) => {
  if (!newValue && oldValue) {
    // Multiroom was deactivated
    clearTransitionTimeout();
    transitionState.value = 'idle';
    // Note: clients are now derived from clientRegistryStore, no need to clear them
    // They will simply not be displayed when multiroom is inactive
  }
});

// Save display cache when real clients are loaded (for zone-aware skeleton on next load)
watch(displayClients, (newClients) => {
  // Only save when we have real data (not placeholders) and not in loading state
  if (!shouldShowLoading.value && newClients.length > 0 && newClients[0].mac_id) {
    multiroomStore.saveDisplayCache(newClients);
  }
}, { deep: true });
</script>

<style scoped>
.clients-list {
  display: flex;
  flex-direction: column;
  position: relative;
}

.clients-wrapper {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}
</style>
