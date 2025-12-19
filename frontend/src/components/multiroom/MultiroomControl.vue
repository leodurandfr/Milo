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
            v-for="(client, index) in displayClients"
            :key="index"
            :client="client"
            :is-loading="shouldShowLoading"
            :zone-clients="getZoneClients(client)"
            @volume-change="handleVolumeChange"
            @mute-toggle="handleMuteToggle"
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

// DSP effects enabled (EQ, compressor, loudness) - volume always uses DSP
const isDspEffectsEnabled = computed(() => dspStore.isDspEffectsEnabled);

// Get linked groups from DSP store - only used when DSP effects are enabled
const linkedGroups = computed(() => {
  // Zone grouping only applies when DSP effects are enabled
  if (!isDspEffectsEnabled.value) return [];
  return dspStore.linkedGroups || [];
});

// Get zone info for a client (returns zone object if linked, null otherwise)
function getZoneForClient(client) {
  const dspId = client.dsp_id;
  if (!dspId) return null;
  for (const group of linkedGroups.value) {
    if (group.client_ids?.includes(dspId)) {
      return group;
    }
  }
  return null;
}

// Check if a client is the "primary" of its zone (first in the list)
function isZonePrimary(client) {
  const zone = getZoneForClient(client);
  if (!zone) return true; // Not in a zone, show it
  return zone.client_ids[0] === client.dsp_id;
}

// Calculate arithmetic average volume for a zone (in dB)
function getZoneAverageVolume(zone) {
  if (!zone?.client_ids?.length) return -30;
  const volumes = zone.client_ids.map(dspId => dspStore.getClientDspVolume(dspId));
  return volumes.reduce((sum, v) => sum + v, 0) / volumes.length;
}

// Check if a zone is muted (ALL clients must be muted)
function getZoneMuted(zone) {
  if (!zone?.client_ids?.length) return false;
  return zone.client_ids.every(dspId => dspStore.getClientDspMute(dspId));
}

// Track starting state when zone slider drag begins
// Structure: { zoneId: { startAvg, clientStarts: { dspId: volume } } }
const zoneSliderState = ref({});

// Get or initialize zone slider state (called on first slider input)
function getZoneSliderState(zone) {
  const zoneId = zone.id || zone.client_ids.join('-');
  if (!zoneSliderState.value[zoneId]) {
    // First input - capture ALL starting volumes
    const clientStarts = {};
    zone.client_ids.forEach(dspId => {
      clientStarts[dspId] = dspStore.getClientDspVolume(dspId);
    });
    const startAvg = Object.values(clientStarts).reduce((s, v) => s + v, 0) / zone.client_ids.length;
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
  // During enabling or loading, show placeholders based on last known display structure
  if (transitionState.value === 'enabling' || (multiroomStore.clients.length === 0 && multiroomStore.isLoading)) {
    return multiroomStore.lastKnownDisplayItems.map((item, i) => ({
      id: `placeholder-${i}`,
      name: '',
      volume: 0,
      dspMuted: false,
      isZone: item.type === 'zone'
    }));
  }

  // Add dspVolume and dspMuted from cache to each client
  // If there are linked groups, filter to show only zone primaries
  if (linkedGroups.value.length > 0) {
    return multiroomStore.clients
      .filter(client => isZonePrimary(client))
      .map(client => {
        const dspVol = dspStore.getClientDspVolume(client.dsp_id);
        const dspMut = dspStore.getClientDspMute(client.dsp_id);
        const zone = getZoneForClient(client);

        if (zone) {
          // This is a zone primary - use custom name or fallback to "Zone X"
          const zoneIndex = linkedGroups.value.indexOf(zone) + 1;
          const zoneName = zone.name || `Zone ${zoneIndex}`;
          const clientNames = dspStore.sortClientIdsLocalFirst(zone.client_ids)
            .map(dspId => {
              // Find client by dsp_id
              const c = multiroomStore.clients.find(cl => cl.dsp_id === dspId);
              return c ? c.name : dspId;
            })
            .join(' · ');
          // Use arithmetic average of all clients in zone
          // Returns null if volumes not yet loaded
          const zoneVolume = getZoneAverageVolume(zone);
          return {
            ...client,
            name: zoneName,
            zoneClients: clientNames,
            dspVolume: zoneVolume,
            dspMuted: getZoneMuted(zone),
            volumeLoading: zoneVolume === null,  // Flag to show loading state
            zoneClientIds: zone.client_ids  // Needed for delta calculation
          };
        }
        return { ...client, dspVolume: dspVol, dspMuted: dspMut };
      });
  }

  // No linked groups - just add dspVolume and dspMuted to each client
  return multiroomStore.clients.map(client => {
    const dspVol = dspStore.getClientDspVolume(client.dsp_id);
    const dspMut = dspStore.getClientDspMute(client.dsp_id);
    return { ...client, dspVolume: dspVol, dspMuted: dspMut };
  });
});

// === HANDLERS ===
async function handleVolumeChange(clientId, volumeDb) {
  // Volume is always in dB, find client and update DSP volume
  const client = multiroomStore.clients.find(c => c.id === clientId);
  if (!client) return;

  // Check if this client is part of a zone
  const zone = getZoneForClient(client);

  if (zone && zone.client_ids.length > 1) {
    // Zone volume change: apply DELTA to preserve relative offsets
    // Get starting state (captures volumes at start of slider drag)
    const state = getZoneSliderState(zone);
    const delta = volumeDb - state.startAvg;

    // Apply delta to each client from their START volume
    const updatePromises = zone.client_ids.map(async (dspId) => {
      const startVol = state.clientStarts[dspId];
      const newVol = Math.max(settingsStore.volumeLimits.min_db, Math.min(settingsStore.volumeLimits.max_db, startVol + delta));
      const success = await dspStore.updateClientDspVolume(dspId, newVol);
      if (success) {
        dspStore.setClientDspVolume(dspId, newVol);
      }
    });
    await Promise.all(updatePromises);

    // Clear state after change completes (slider drag ended)
    clearZoneSliderState(zone);
  } else {
    // Single client, update directly
    const success = await dspStore.updateClientDspVolume(client.dsp_id, volumeDb);
    if (success) {
      dspStore.setClientDspVolume(client.dsp_id, volumeDb);
    }
  }
}

async function handleMuteToggle(clientId, muted) {
  const client = multiroomStore.clients.find(c => c.id === clientId);
  if (!client) return;

  // Check if this client is part of a zone
  const zone = getZoneForClient(client);

  if (zone && zone.client_ids.length > 1) {
    // Zone mute: mute ALL clients in the zone
    const updatePromises = zone.client_ids.map(async (dspId) => {
      await dspStore.updateClientDspMute(dspId, muted);
    });
    await Promise.all(updatePromises);
  } else {
    // Single client
    await dspStore.updateClientDspMute(client.dsp_id, muted);
  }
}

// === TRANSITION HELPERS ===
function startTransitionTimeout() {
  clearTransitionTimeout();
  transitionTimeoutId = setTimeout(() => {
    if (transitionState.value === 'enabling' || transitionState.value === 'disabling') {
      console.warn('[MultiroomControl] Transition timeout reached');
      transitionState.value = 'error';
      errorMessage.value = t('multiroom.timeout_error');
      multiroomStore.isLoading = false;
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
function handleClientConnected(event) {
  if (transitionState.value !== 'disabling') {
    multiroomStore.handleClientConnected(event);
  }
}

function handleClientDisconnected(event) {
  if (transitionState.value !== 'disabling') {
    multiroomStore.handleClientDisconnected(event);
  }
}

function handleClientVolumeChanged(event) {
  multiroomStore.handleClientVolumeChanged(event);
}

function handleClientNameChanged(event) {
  multiroomStore.handleClientNameChanged(event);
}

function handleClientMuteChanged(event) {
  multiroomStore.handleClientMuteChanged(event);
}

function handleSystemStateChanged(event) {
  unifiedStore.updateState(event);
}

function handleMultiroomEnabling() {
  transitionState.value = 'enabling';
  errorMessage.value = '';
  multiroomStore.isLoading = true;
  multiroomStore.clearCache();
  startTransitionTimeout();
}

function handleMultiroomDisabling() {
  transitionState.value = 'disabling';
  errorMessage.value = '';
  multiroomStore.isLoading = false;
  startTransitionTimeout();
}

async function handleMultiroomReady() {
  clearTransitionTimeout();

  // Load clients now that services are ready
  await multiroomStore.loadClients(true); // forceNoCache=true

  // Refresh DSP volumes from all clients (volumes were just pushed by backend)
  await dspStore.loadAllClientDspVolumes(multiroomStore.clients);

  transitionState.value = 'idle';
}

function handleMultiroomError(event) {
  console.error('[MultiroomControl] Received multiroom_error event:', event);
  clearTransitionTimeout();
  transitionState.value = 'error';
  errorMessage.value = event?.message || t('multiroom.error');
  multiroomStore.isLoading = false;
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

  // Load linked groups and DSP volumes if DSP effects are enabled
  if (dspStore.isDspEffectsEnabled) {
    await dspStore.loadTargets();
    // Fetch actual DSP volumes from all clients
    await dspStore.loadAllClientDspVolumes(multiroomStore.clients);
  }

  unsubscribeFunctions.push(
    on('snapcast', 'client_connected', handleClientConnected),
    on('snapcast', 'client_disconnected', handleClientDisconnected),
    on('snapcast', 'client_volume_changed', handleClientVolumeChanged),
    on('snapcast', 'client_name_changed', handleClientNameChanged),
    on('snapcast', 'client_mute_changed', handleClientMuteChanged),
    on('system', 'state_changed', handleSystemStateChanged),
    on('routing', 'multiroom_enabling', handleMultiroomEnabling),
    on('routing', 'multiroom_disabling', handleMultiroomDisabling),
    on('routing', 'multiroom_ready', handleMultiroomReady),
    on('routing', 'multiroom_error', handleMultiroomError),
    // DSP events for linked groups updates
    on('dsp', 'links_changed', (e) => dspStore.handleLinksChanged(e)),
    on('dsp', 'enabled_changed', (e) => dspStore.handleEnabledChanged(e)),
    // DSP client volumes pushed (when multiroom activates)
    on('dsp', 'client_volumes_pushed', (e) => dspStore.handleClientVolumesPushed(e)),
    // Volume changes from other UIs (DspModal/ZoneTabs)
    on('volume', 'volume_changed', (event) => {
      if (event.data?.client_volumes) {
        for (const [hostname, volumeDb] of Object.entries(event.data.client_volumes)) {
          dspStore.setClientDspVolume(hostname, volumeDb);
        }
      }
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
    // Clear clients after deactivation
    multiroomStore.clients = [];
    multiroomStore.clearCache();
  }
});

// Save display cache when real clients are loaded (for zone-aware skeleton on next load)
watch(displayClients, (newClients) => {
  // Only save when we have real data (not placeholders) and not in loading state
  if (!shouldShowLoading.value && newClients.length > 0 && !newClients[0].id?.startsWith('placeholder-')) {
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
