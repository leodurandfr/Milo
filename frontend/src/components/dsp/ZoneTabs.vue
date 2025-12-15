<!-- frontend/src/components/dsp/ZoneTabs.vue -->
<!-- Zone tabs + Volume controls for zones/clients -->
<template>
  <section class="settings-section">
    <div class="section-group">
      <!-- Section Header: "Zones" -->
      <div class="section-header">
        <h2 class="heading-2">{{ zoneTabs.length === 1 ? zoneTabs[0].label : $t('dsp.zones.title', 'Zones') }}</h2>
      </div>

      <!-- Zone/Client Tabs (hidden when single target) -->
      <Tabs
        v-if="zoneTabs.length > 1"
        v-model="selectedTargetLocal"
        :tabs="zoneTabs"
        size="small"
        @change="handleTargetChange"
      />

      <!-- Volume Controls -->
      <div class="volume-controls">
      <!-- Case A: Zone selected (multiple clients) -->
      <template v-if="isZoneSelected && zoneClients.length > 1">
        <span class="volume-subtitle text-mono">{{ $t('dsp.volume.title', 'Volume') }}</span>
        <div class="clients-list">
          <div
            v-for="(client, index) in zoneClients"
            :key="client.id"
            class="client-volume-row"
            :class="{ 'client-muted': getClientMute(client.id) }"
          >
            <!-- Separator line above each row -->
            <div class="client-separator"></div>
            <span class="client-name heading-3" :class="{ 'muted': getClientMute(client.id) }">
              {{ client.name }}
            </span>
            <div class="volume-control">
              <RangeSlider
                :model-value="getClientVolume(client.id)"
                :min="settingsStore.volumeLimits.min_db"
                :max="settingsStore.volumeLimits.max_db"
                :step="1"
                show-value
                value-unit=" dB"
                :disabled="getClientMute(client.id) || disabled"
                @input="(v) => handleVolumeInput(client.id, v)"
                @change="(v) => handleVolumeChange(client.id, v)"
              />
            </div>
            <Toggle
              :model-value="!getClientMute(client.id)"
              type="background-strong"
              @change="(enabled) => handleMuteToggle(client.id, !enabled)"
            />
          </div>
        </div>
      </template>

      <!-- Case B: Single client selected (or zone with single client) -->
      <template v-else>
        <div class="single-client-row">
          <span class="volume-label heading-3" :class="{ 'muted': currentTargetMute }">
            {{ $t('dsp.volume.title', 'Volume') }}
          </span>
          <div class="volume-control">
            <RangeSlider
              :model-value="currentTargetVolume"
              :min="settingsStore.volumeLimits.min_db"
              :max="settingsStore.volumeLimits.max_db"
              :step="1"
              show-value
              value-unit=" dB"
              :disabled="currentTargetMute || disabled"
              @input="(v) => handleVolumeInput(selectedTargetLocal, v)"
              @change="(v) => handleVolumeChange(selectedTargetLocal, v)"
            />
          </div>
          <Toggle
            :model-value="!currentTargetMute"
            type="background-strong"
            @change="(enabled) => handleMuteToggle(selectedTargetLocal, !enabled)"
          />
        </div>
      </template>
      </div>
    </div>
  </section>
</template>

<script setup>
import { ref, computed, watch } from 'vue';
import { useDspStore } from '@/stores/dspStore';
import { useSettingsStore } from '@/stores/settingsStore';
import Tabs from '@/components/ui/Tabs.vue';
import RangeSlider from '@/components/ui/RangeSlider.vue';
import Toggle from '@/components/ui/Toggle.vue';

const dspStore = useDspStore();
const settingsStore = useSettingsStore();

const props = defineProps({
  disabled: {
    type: Boolean,
    default: false
  }
});

const emit = defineEmits(['targetChange']);

// Local state
const selectedTargetLocal = ref(dspStore.selectedTarget);

// Throttling for volume updates
const volumeThrottleMap = new Map();

// === COMPUTED ===
const targets = computed(() => dspStore.availableTargets);
const hasMultipleTargets = computed(() => targets.value.length > 1);

// Convert targets to Tabs format (zones + individual clients)
const zoneTabs = computed(() => {
  const tabs = [];

  // Group linked clients into zones
  const processedIds = new Set();

  for (const target of targets.value) {
    if (processedIds.has(target.id)) continue;

    const linkedIds = dspStore.getLinkedClientIds(target.id);

    if (linkedIds.length > 1) {
      // This is a zone - get names of linked clients
      const linkedClients = linkedIds
        .map(id => targets.value.find(t => t.id === id))
        .filter(Boolean);

      // Find the group for this zone to get custom name
      const group = dspStore.getZoneGroup(target.id);

      // Use custom zone name if set, otherwise combine client names
      const zoneName = group?.name || (linkedClients.length > 0
        ? linkedClients.map(c => c.name).join(' + ')
        : target.name);

      tabs.push({
        label: zoneName,
        value: `zone:${linkedIds.join(',')}`,
        badge: 'link',
        disabled: linkedClients.some(c => !c.available),
        groupId: group?.id || null
      });

      // Mark all linked clients as processed
      linkedIds.forEach(id => processedIds.add(id));
    } else {
      // Individual client
      tabs.push({
        label: target.name,
        value: target.id,
        disabled: !target.available
      });
      processedIds.add(target.id);
    }
  }

  return tabs;
});

// Check if current selection is a zone (multiple linked clients)
const isZoneSelected = computed(() => {
  return selectedTargetLocal.value.startsWith('zone:');
});

// Get clients in the selected zone
const zoneClients = computed(() => {
  if (!isZoneSelected.value) {
    // Single client - return it
    const target = targets.value.find(t => t.id === selectedTargetLocal.value);
    return target ? [target] : [];
  }

  // Parse zone client IDs
  const clientIds = selectedTargetLocal.value.replace('zone:', '').split(',');
  return clientIds
    .map(id => targets.value.find(t => t.id === id))
    .filter(Boolean);
});

// Current target volume/mute for single client mode
const currentTargetVolume = computed(() => {
  if (isZoneSelected.value && zoneClients.value.length > 0) {
    return getClientVolume(zoneClients.value[0].id);
  }
  return dspStore.getClientDspVolume(selectedTargetLocal.value);
});

const currentTargetMute = computed(() => {
  if (isZoneSelected.value && zoneClients.value.length > 0) {
    return getClientMute(zoneClients.value[0].id);
  }
  return dspStore.getClientDspMute(selectedTargetLocal.value);
});

// Selected zone/client name for display in other sections
const selectedZoneName = computed(() => {
  const tab = zoneTabs.value.find(t => t.value === selectedTargetLocal.value);
  return tab ? tab.label : '';
});

// === VOLUME HELPERS ===
function getClientVolume(clientId) {
  return Math.round(dspStore.getClientDspVolume(clientId));
}

function getClientMute(clientId) {
  return dspStore.getClientDspMute(clientId);
}

// === HANDLERS ===
async function handleTargetChange(targetValue) {
  selectedTargetLocal.value = targetValue;

  // If it's a zone, select the first client as the active DSP target
  if (targetValue.startsWith('zone:')) {
    const clientIds = targetValue.replace('zone:', '').split(',');
    if (clientIds.length > 0) {
      await dspStore.selectTarget(clientIds[0]);
    }
  } else {
    await dspStore.selectTarget(targetValue);
  }

  emit('targetChange', targetValue);
}

function handleVolumeInput(clientId, value) {
  // Update local cache immediately for responsive UI
  dspStore.setClientDspVolume(clientId, value);

  // Throttle API calls
  let throttleState = volumeThrottleMap.get(clientId) || {};

  if (throttleState.throttleTimeout) clearTimeout(throttleState.throttleTimeout);
  if (throttleState.finalTimeout) clearTimeout(throttleState.finalTimeout);

  throttleState.throttleTimeout = setTimeout(() => {
    dspStore.updateClientDspVolume(clientId, value);
  }, 50);

  throttleState.finalTimeout = setTimeout(() => {
    dspStore.updateClientDspVolume(clientId, value);
  }, 200);

  volumeThrottleMap.set(clientId, throttleState);
}

function handleVolumeChange(clientId, value) {
  // Clear throttle timers
  const throttleState = volumeThrottleMap.get(clientId);
  if (throttleState) {
    if (throttleState.throttleTimeout) clearTimeout(throttleState.throttleTimeout);
    if (throttleState.finalTimeout) clearTimeout(throttleState.finalTimeout);
    volumeThrottleMap.delete(clientId);
  }

  // Final update
  dspStore.updateClientDspVolume(clientId, value);
}

async function handleMuteToggle(clientId, muted) {
  dspStore.setClientDspMute(clientId, muted);

  // Update via API
  if (clientId === 'local' || clientId === dspStore.selectedTarget) {
    await dspStore.updateDspMute(muted);
  } else {
    // For remote clients, we need to call the specific endpoint
    // This is handled through the DSP store's client API
    try {
      const axios = (await import('axios')).default;
      await axios.put(`/api/dsp/client/${clientId}/mute`, { muted });
    } catch (error) {
      console.error(`Error updating mute for ${clientId}:`, error);
    }
  }
}

// Sync local target with store
watch(() => dspStore.selectedTarget, (newTarget) => {
  // Don't override if we have a zone selected
  if (!selectedTargetLocal.value.startsWith('zone:')) {
    selectedTargetLocal.value = newTarget;
  }
});

// Auto-select first tab if current selection doesn't exist
watch(zoneTabs, (tabs) => {
  if (tabs.length > 0) {
    const currentTabExists = tabs.some(t => t.value === selectedTargetLocal.value);
    if (!currentTabExists) {
      selectedTargetLocal.value = tabs[0].value;
      handleTargetChange(tabs[0].value);
    }
  }
}, { immediate: true });

// Expose selectedZoneName for parent components
defineExpose({ selectedZoneName });
</script>

<style scoped>
/* Settings section pattern from VolumeSettings.vue */
.settings-section {
  background: var(--color-background-neutral);
  border-radius: var(--radius-06);
  padding: var(--space-05-fixed) var(--space-05);
  display: flex;
  flex-direction: column;
  gap: var(--space-05-fixed);
}

.section-group {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

/* Section Header */
.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-03);
}

/* Volume Controls */
.volume-controls {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

.volume-subtitle {
  color: var(--color-text-secondary);
}

/* Clients list (for zones) */
.clients-list {
  display: flex;
  flex-direction: column;
}

.client-volume-row {
  display: grid;
  grid-template-columns: minmax(100px, 150px) 1fr auto;
  align-items: center;
  gap: var(--space-03);
  padding: var(--space-03) 0;
  position: relative;
}

.client-volume-row:last-child {
  padding-bottom: 0;
}

.client-separator {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 1px;
  background: var(--color-border);
}

.client-name {
  color: var(--color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  transition: color var(--transition-fast);
}

.client-name.muted {
  color: var(--color-text-light);
}

/* Single client row */
.single-client-row {
  display: grid;
  grid-template-columns: minmax(80px, 100px) 1fr auto;
  align-items: center;
  gap: var(--space-03);
}

.volume-label {
  color: var(--color-text);
  transition: color var(--transition-fast);
}

.volume-label.muted {
  color: var(--color-text-light);
}

.volume-control {
  flex: 1;
  min-width: 0;
}

/* Mobile adjustments */
@media (max-aspect-ratio: 4/3) {
  .settings-section {
    border-radius: var(--radius-05);
  }

  .client-volume-row {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-02);
    padding: var(--space-02) 0;
  }

  .client-name {
    flex: 1;
    min-width: 0;
  }

  .volume-control {
    order: 3;
    width: 100%;
    flex-basis: 100%;
  }

  .single-client-row {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-02);
  }

  .single-client-row .volume-label {
    flex: 1;
  }

  .single-client-row .volume-control {
    order: 3;
    width: 100%;
    flex-basis: 100%;
  }
}
</style>
