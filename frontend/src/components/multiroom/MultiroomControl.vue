<!-- frontend/src/components/multiroom/MultiroomControl.vue -->
<template>
  <div class="clients-container">
    <div class="clients-list">
      <!-- MESSAGE: Multiroom disabled or error -->
      <MessageContent v-if="showMessage" :icon="messageIcon" :title="messageTitle" />

      <!-- CLIENTS: Skeletons OR real items -->
      <div v-else ref="clientsWrapperRef" class="clients-wrapper">
        <MultiroomItem
          v-for="client in displayClients"
          :key="client.mac_id || client.id"
          :client="client"
          :is-loading="delayedLoading"
          :is-zone="client.isZone || false"
          :zone-client-details="client.zoneClientDetails || null"
          @volume-change="handleVolumeChange"
          @mute-toggle="handleMuteToggle"
          @client-volume-change="handleClientVolumeChange"
          @client-mute-toggle="handleClientMuteToggle"
        />
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch, nextTick } from 'vue';
import { useI18n } from '@/services/i18n';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useMultiroomStore } from '@/stores/multiroomStore';
import { useSnapcastStore } from '@/stores/snapcastStore';
import { useEqualizerStore } from '@/stores/equalizerStore';
import { DEFAULT_VOLUME_DB } from '@/constants/volume';
import { logger } from '@/services/logger';
import MultiroomItem from './MultiroomItem.vue';
import MessageContent from '@/components/ui/MessageContent.vue';

const { t } = useI18n();
const unifiedStore = useUnifiedAudioStore();
const multiroomStore = useMultiroomStore();
const snapcastStore = useSnapcastStore();
const equalizerStore = useEqualizerStore();

const clientsWrapperRef = ref(null);

// === COMPUTED ===
const isMultiroomActive = computed(() => unifiedStore.systemState.multiroom_enabled);

const linkedGroups = computed(() => multiroomStore.zoneList);

// Get zone info for a client (returns zone object if linked, null otherwise)
const getZoneForClient = (client) => multiroomStore.getZoneForClient(client.mac_id);

// Check if a client is the "primary" of its zone (first online in the list)
function isZonePrimary(client) {
  const zone = getZoneForClient(client);
  if (!zone) return true; // Not in a zone, show it

  // Find the first online client in the zone
  const firstOnlineId = zone.client_ids.find(macId =>
    snapcastStore.clients.some(c => c.mac_id === macId)
  );

  return firstOnlineId === client.mac_id;
}

// A zone's figures are the server's, computed over the members it counts.
// Recomputing them here counted another set. A zone the volume state does not
// hold yet (created a moment before its first broadcast) sits where the
// server puts a zone with no member to count: DEFAULT_VOLUME_DB.
function getZoneAverageVolume(zone) {
  return unifiedStore.volumeState.zones[zone.id]?.average_volume_db ?? DEFAULT_VOLUME_DB;
}

function getZoneMuted(zone) {
  return unifiedStore.volumeState.zones[zone.id]?.all_muted ?? false;
}

const showMessage = computed(() => {
  // Show message when:
  // - Error state
  // - Disabling (show "disabled" message immediately)
  // - Multiroom is off and not enabling
  if (multiroomStore.transitionState === 'error') {
    return true;
  }
  if (multiroomStore.transitionState === 'disabling') {
    return true;
  }
  if (multiroomStore.transitionState === 'enabling') {
    return false;
  }
  return !isMultiroomActive.value;
});

const messageIcon = computed(() => {
  return multiroomStore.transitionState === 'error' ? 'error' : 'multiroom';
});

const messageTitle = computed(() => {
  if (multiroomStore.transitionState === 'error') {
    // transitionError holds a localized message resolved in the store.
    return multiroomStore.transitionError || t('multiroom.error');
  }
  return t('multiroom.disabled');
});

// Show loading skeletons during enabling or store loading
const shouldShowLoading = computed(() => {
  return multiroomStore.transitionState === 'enabling' || snapcastStore.isLoading;
});

// Delayed loading for per-element cross-fade: stays true one extra tick after data arrives,
// so items render with skeletons first, then isLoading toggles on existing elements,
// triggering the CSS opacity transitions on each skeleton/content pair in MultiroomItem
const delayedLoading = ref(shouldShowLoading.value);

watch(shouldShowLoading, (loading) => {
  if (loading) {
    delayedLoading.value = true;
  } else {
    // Double rAF ensures the browser paints the skeleton state before toggling,
    // so CSS opacity transitions have an initial state to animate from
    requestAnimationFrame(() => {
      requestAnimationFrame(() => { delayedLoading.value = false; });
    });
  }
});

const displayClients = computed(() => {
  // Force Vue to track volumeState.zones and volumeState.clients as dependencies
  // This ensures recomputation when zone averages or client volumes change
  void unifiedStore.volumeState.zones;
  void unifiedStore.volumeState.clients;

  // During enabling or loading, show placeholders based on last known display structure
  if (multiroomStore.transitionState === 'enabling' || (snapcastStore.clients.length === 0 && snapcastStore.isLoading)) {
    return snapcastStore.lastKnownDisplayItems.map((item, i) => ({
      id: `placeholder-${i}`,
      mac_id: item.mac_id || null,
      name: '',
      // The skeleton's slider needs a number to draw; it shows no value.
      equalizerVolume: DEFAULT_VOLUME_DB,
      equalizerMuted: false,
      isZone: item.type === 'zone',
      zoneClientDetails: null
    }));
  }

  // Add equalizerVolume and equalizerMuted from cache to each client
  // If there are linked groups, filter to show only zone primaries
  if (linkedGroups.value.length > 0) {
    return snapcastStore.clients
      .filter(client => isZonePrimary(client))
      .map(client => {
        const eqVol = unifiedStore.getClientVolume(client.mac_id);
        const eqMut = unifiedStore.getClientMute(client.mac_id);
        const zone = getZoneForClient(client);

        if (zone) {
          // This is a zone primary - use custom name or fallback to "Zone X"
          const zoneIndex = linkedGroups.value.indexOf(zone) + 1;
          const zoneName = zone.name || `Zone ${zoneIndex}`;
          // Filter from already-sorted clients list (local first, online first)
          const zoneClientIds = new Set(zone.client_ids);
          const zoneClientsFiltered = snapcastStore.clients.filter(c => zoneClientIds.has(c.mac_id));

          // Build detailed client list for expanded view
          const zoneClientDetails = zoneClientsFiltered.map(c => ({
            id: c.mac_id,
            mac_id: c.mac_id,
            name: c.name,
            equalizerVolume: unifiedStore.getClientVolume(c.mac_id),
            equalizerMuted: unifiedStore.getClientMute(c.mac_id),
            speakerType: equalizerStore.getClientSpeakerType(c.mac_id),
            online: c.online,
            is_local: c.is_local,
            volume_control: c.volume_control
          }));

          // Use arithmetic average of all clients in zone
          const zoneVolume = getZoneAverageVolume(zone);
          return {
            ...client,
            name: zoneName,
            equalizerVolume: zoneVolume,
            equalizerMuted: getZoneMuted(zone),
            zoneClientIds: zone.client_ids,
            isZone: true,
            all_external_volume: zone.all_external_volume || false,
            zoneClientDetails
          };
        }
        return {
          ...client,
          equalizerVolume: eqVol,
          equalizerMuted: eqMut,
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

  // No linked groups - just add equalizerVolume and equalizerMuted to each client
  // Sorting handled by multiroomStore (local first, online first, alphabetical)
  return snapcastStore.clients.map(client => {
    const eqVol = unifiedStore.getClientVolume(client.mac_id);
    const eqMut = unifiedStore.getClientMute(client.mac_id);
    return {
      ...client,
      equalizerVolume: eqVol,
      equalizerMuted: eqMut,
      isZone: false,
      zoneClientDetails: null
    };
  });
});

// Which rows are shown, under which names — everything the two watchers below
// react to. displayClients itself is rebuilt on every volume WS event (a level
// is one of its fields), and watching it was a localStorage write plus a full
// re-measure of the name column for each one, several times a second while a
// slider moves.
const displayStructure = computed(() => JSON.stringify(
  displayClients.value.map(client => [
    client.mac_id || client.id,
    client.isZone || false,
    client.name,
    (client.zoneClientDetails || []).map(zoneClient => zoneClient.name)
  ])
));

// === NAME WIDTH SYNCHRONIZATION ===
// Align all name columns to the widest name (max 200px)
function updateNameWidth() {
  const wrapper = clientsWrapperRef.value;
  if (!wrapper || shouldShowLoading.value) return;

  // Collect all visible names from data
  const allNames = [];
  for (const client of displayClients.value) {
    if (client.name) allNames.push(client.name);
    if (client.zoneClientDetails) {
      for (const zc of client.zoneClientDetails) {
        if (zc.name) allNames.push(zc.name);
      }
    }
  }
  if (allNames.length === 0) return;

  // Measure using off-screen element with same typography
  const measure = document.createElement('span');
  measure.className = 'heading-3';
  measure.style.cssText = 'position:absolute;visibility:hidden;white-space:nowrap;pointer-events:none';
  wrapper.appendChild(measure);

  let maxWidth = 0;
  for (const name of allNames) {
    measure.textContent = name;
    maxWidth = Math.max(maxWidth, measure.offsetWidth);
  }
  measure.remove();

  if (maxWidth > 0) {
    wrapper.style.setProperty('--name-width', `${Math.min(maxWidth, 200)}px`);
  }
}

// === HANDLERS ===
async function handleVolumeChange(clientMacId, volumeDb, options = {}) {
  const { isZone = false } = options;

  // Find client by mac_id (unique identifier for all clients)
  const client = snapcastStore.clients.find(c => c.mac_id === clientMacId);
  if (!client) return;

  // Use explicit isZone flag instead of recalculating zone membership
  if (isZone) {
    const zone = getZoneForClient(client);
    if (zone && zone.client_ids.length > 1) {
      try {
        await unifiedStore.setZoneVolume(zone.id, volumeDb);
      } catch (error) {
        logger.error('multiroom', 'Failed to set zone volume', error);
      }
    }
  } else {
    // Standalone client - always use direct update
    await unifiedStore.setClientVolume(client.mac_id, volumeDb);
    // Volume state will be updated via WebSocket broadcast
  }
}

async function handleMuteToggle(clientMacId, muted, options = {}) {
  const { isZone = false } = options;

  // Find client by mac_id (unique identifier for all clients)
  const client = snapcastStore.clients.find(c => c.mac_id === clientMacId);
  if (!client) return;

  // Use explicit isZone flag instead of recalculating zone membership
  if (isZone) {
    const zone = getZoneForClient(client);
    if (zone && zone.client_ids.length > 1) {
      await unifiedStore.setZoneMute(zone.id, muted);
    }
  } else {
    // Standalone client - always use direct update
    await unifiedStore.setClientMute(client.mac_id, muted);
  }
}

// Handle individual client volume change (within expanded zone)
async function handleClientVolumeChange(clientEqId, volumeDb) {
  await unifiedStore.setClientVolume(clientEqId, volumeDb);
}

// Handle individual client mute toggle (within expanded zone)
async function handleClientMuteToggle(clientEqId, muted) {
  await unifiedStore.setClientMute(clientEqId, muted);
}

// === LIFECYCLE ===
onMounted(async () => {
  // Reset any stale error state from a previous session
  if (multiroomStore.transitionState === 'error') {
    multiroomStore.resetTransition();
  }

  // Preload display cache for zone-aware skeletons
  snapcastStore.preloadDisplayCache();

  if (isMultiroomActive.value) {
    await snapcastStore.loadClients();
  }

  // Load linked groups (zones are a multiroom feature, independent of equalizer effects)
  await equalizerStore.loadTargets();

  // Synchronize name column widths after all data is loaded
  nextTick(updateNameWidth);
});

// Reload clients when multiroom becomes ready after a transition
watch(() => multiroomStore.transitionState, (newState, oldState) => {
  if (newState === 'idle' && (oldState === 'enabling' || oldState === 'disabling')) {
    snapcastStore.loadClients();
  }
});

// Watch for deactivation completion — reset store transition as safety net
watch(isMultiroomActive, (newValue, oldValue) => {
  if (!newValue && oldValue) {
    multiroomStore.resetTransition();
  }
});

// Synchronize name column widths when clients load or loading finishes
watch([displayStructure, shouldShowLoading], () => { nextTick(updateNameWidth); });

// Save display cache when real clients are loaded (for zone-aware skeleton on next load)
watch(displayStructure, () => {
  const items = displayClients.value;
  // Only save when we have real data (not placeholders) and not in loading state
  if (!shouldShowLoading.value && items.length > 0 && items[0].mac_id) {
    snapcastStore.saveDisplayCache(items);
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
