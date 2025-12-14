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
          <MultiroomClientItem
            v-for="(client, index) in displayClients"
            :key="index"
            :client="client"
            :is-loading="shouldShowLoading"
            :zone-badge="getZoneBadge(client)"
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
import useWebSocket from '@/services/websocket';
import MultiroomClientItem from './MultiroomClientItem.vue';
import MessageContent from '@/components/ui/MessageContent.vue';

const { t } = useI18n();
const unifiedStore = useUnifiedAudioStore();
const multiroomStore = useMultiroomStore();
const dspStore = useDspStore();
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
const isDspEffectsEnabled = computed(() => dspStore.isDspEnabled);

// Get linked groups from DSP store - only used when DSP effects are enabled
const linkedGroups = computed(() => {
  // Zone grouping only applies when DSP effects are enabled
  if (!isDspEffectsEnabled.value) return [];
  return dspStore.linkedGroups || [];
});

// Normalize hostname: linkedGroups uses "local" but clients use "milo" for local host
function normalizeHostname(hostname) {
  return hostname === 'milo' ? 'local' : hostname;
}

// Denormalize hostname: convert "local" back to "milo" for client lookup
function denormalizeHostname(hostname) {
  return hostname === 'local' ? 'milo' : hostname;
}

// Get zone info for a client (returns zone object if linked, null otherwise)
function getZoneForClient(hostname) {
  const normalized = normalizeHostname(hostname);
  for (const group of linkedGroups.value) {
    if (group.client_ids && group.client_ids.includes(normalized)) {
      return group;
    }
  }
  return null;
}

// Check if a client is the "primary" of its zone (first in the list)
function isZonePrimary(client) {
  const hostname = client.host || '';
  const zone = getZoneForClient(hostname);
  if (!zone) return true; // Not in a zone, show it
  // Compare normalized hostname with first client in zone
  const normalized = normalizeHostname(hostname);
  return zone.client_ids[0] === normalized;
}

// Get zone badge for display (shows client names or count)
function getZoneBadge(client) {
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
  // During enabling, show placeholders for skeleton loading
  if (transitionState.value === 'enabling') {
    return Array.from({ length: multiroomStore.lastKnownClientCount }, (_, i) => ({
      id: `placeholder-${i}`,
      name: '',
      volume: 0,
      muted: false
    }));
  }

  // When loading with no clients, show skeleton placeholders
  if (multiroomStore.clients.length === 0 && multiroomStore.isLoading) {
    return Array.from({ length: multiroomStore.lastKnownClientCount }, (_, i) => ({
      id: `placeholder-${i}`,
      name: '',
      volume: 0,
      muted: false
    }));
  }

  // Add dspVolume from cache to each client (volume always in dB via DSP)
  // If there are linked groups, filter to show only zone primaries
  if (linkedGroups.value.length > 0) {
    return multiroomStore.clients
      .filter(client => isZonePrimary(client))
      .map(client => {
        // Use IP for remote clients, 'local' for main Milo
        const hostname = client.host === 'milo' ? 'local' : (client.ip || client.host || '');
        const dspVol = dspStore.getClientDspVolume(hostname);
        const zone = getZoneForClient(client.host || '');

        if (zone) {
          // This is a zone primary - show as "Zone X" with client names
          const zoneIndex = linkedGroups.value.indexOf(zone) + 1;
          const clientNames = zone.client_ids
            .map(h => {
              // Denormalize hostname for client lookup (zone uses "local", clients use "milo")
              const lookupHost = denormalizeHostname(h);
              const c = multiroomStore.clients.find(cl => cl.host === lookupHost);
              return c ? c.name : h;
            })
            .join(', ');
          return {
            ...client,
            name: `Zone ${zoneIndex}`,
            zoneClients: clientNames,
            dspVolume: dspVol
          };
        }
        return { ...client, dspVolume: dspVol };
      });
  }

  // No linked groups - just add dspVolume to each client
  return multiroomStore.clients.map(client => {
    // Use IP for remote clients, 'local' for main Milo
    const hostname = client.host === 'milo' ? 'local' : (client.ip || client.host || '');
    const dspVol = dspStore.getClientDspVolume(hostname);
    return { ...client, dspVolume: dspVol };
  });
});

// === HANDLERS ===
async function handleVolumeChange(clientId, volumeDb) {
  // Volume is always in dB, find client hostname and update DSP volume
  const client = multiroomStore.clients.find(c => c.id === clientId);
  if (!client) return;

  // Use IP for remote clients, 'local' for main Milo
  const hostname = client.host === 'milo' ? 'local' : (client.ip || client.host);

  // Check if this client is part of a zone
  const zone = getZoneForClient(hostname);

  if (zone && zone.client_ids.length > 1) {
    // Update all clients in the zone
    const updatePromises = zone.client_ids.map(async (zoneHostname) => {
      const success = await dspStore.updateClientDspVolume(zoneHostname, volumeDb);
      if (success) {
        // Update local cache for immediate UI feedback
        dspStore.setClientDspVolume(zoneHostname, volumeDb);
      }
    });
    await Promise.all(updatePromises);
  } else {
    // Single client, update directly
    const success = await dspStore.updateClientDspVolume(hostname, volumeDb);
    if (success) {
      dspStore.setClientDspVolume(hostname, volumeDb);
    }
  }
}

async function handleMuteToggle(clientId, muted) {
  await multiroomStore.toggleClientMute(clientId, muted);
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
  console.log('[MultiroomControl] Received multiroom_enabling event');
  transitionState.value = 'enabling';
  errorMessage.value = '';
  multiroomStore.isLoading = true;
  multiroomStore.clearCache();
  startTransitionTimeout();
}

function handleMultiroomDisabling() {
  console.log('[MultiroomControl] Received multiroom_disabling event');
  transitionState.value = 'disabling';
  errorMessage.value = '';
  multiroomStore.isLoading = false;
  startTransitionTimeout();
}

async function handleMultiroomReady() {
  console.log('[MultiroomControl] Received multiroom_ready event');
  clearTransitionTimeout();

  // Load clients now that services are ready
  await multiroomStore.loadClients(true); // forceNoCache=true

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

  // Load linked groups and DSP volumes if DSP is enabled
  if (dspStore.isDspEnabled) {
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
    on('dsp', 'enabled_changed', (e) => dspStore.handleEnabledChanged(e))
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
    console.log('[MultiroomControl] Multiroom deactivated');
    clearTransitionTimeout();
    transitionState.value = 'idle';
    // Clear clients after deactivation
    multiroomStore.clients = [];
    multiroomStore.clearCache();
  }
});
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
