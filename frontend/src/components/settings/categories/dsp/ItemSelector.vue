<!-- frontend/src/components/settings/categories/dsp/ItemSelector.vue -->
<!-- Zone/Client selector with volume controls -->
<template>
  <div class="item-selector">
    <!-- Tabs section (only when multiple items) -->
    <section v-if="zoneTabs.length > 1" class="settings-section tabs-section">
      <div class="tabs-container">
        <button
          v-for="tab in zoneTabs"
          :key="tab.value"
          type="button"
          class="tab-button heading-4"
          :class="{
            'tab-button--active': selectedTargetLocal === tab.value,
            'tab-button--disabled': tab.disabled
          }"
          :disabled="tab.disabled"
          @click="handleTargetChange(tab.value)"
        >
          <SvgIcon v-if="tab.badge" :name="tab.badge" :size="12" class="tab-badge" />
          {{ tab.label }}
        </button>
      </div>
    </section>

    <!-- Volume section -->
    <section class="settings-section volume-section">
      <!-- CAS 1: Single client (no zone) -->
      <template v-if="!isZoneSelected && zoneClients.length === 1">
        <div class="single-client-row">
          <div class="client-icon-container">
            <SvgIcon :name="getSpeakerIcon(zoneClients[0].id)" :size="28" />
          </div>
          <div class="client-info">
            <span class="client-name heading-3">{{ zoneClients[0].name }}</span>
            <span class="volume-label text-mono">{{ $t('dsp.volume.title', 'Volume') }}</span>
          </div>
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
            variant="secondary"
            @change="(enabled) => handleMuteToggle(selectedTargetLocal, !enabled)"
          />
        </div>
      </template>

      <!-- CAS 2 & 3: Zone selected (single or multiple zones) -->
      <template v-else>
        <!-- Zone header with volume label -->
        <div class="zone-header">
          <div class="zone-header__info">
            <h2 class="heading-2">{{ currentZoneName }}</h2>
            <span class="volume-label text-mono">{{ $t('dsp.volume.title', 'Volume') }}</span>
          </div>
          <Button
            v-if="currentZoneGroupId"
            variant="background-strong"
            size="small"
            @click="handleConfigureZone"
          >
            {{ $t('dsp.zones.configure', 'Configure zone') }}
          </Button>
        </div>

        <!-- Clients list -->
        <div class="clients-list">
          <template v-for="(client, index) in zoneClients" :key="client.id">
            <div class="client-separator"></div>
            <div class="client-volume-row">
            <div class="client-icon-container">
              <SvgIcon :name="getSpeakerIcon(client.id)" :size="28" />
            </div>
            <span class="client-name heading-4" :class="{ 'muted': getClientMute(client.id), 'offline': !client.available }">
              {{ client.name }}
              <span v-if="!client.available" class="badge-offline">{{ $t('dsp.linkedClients.offline', 'Offline') }}</span>
            </span>
            <div class="volume-control">
              <RangeSlider
                :model-value="getClientVolume(client.id)"
                :min="settingsStore.volumeLimits.min_db"
                :max="settingsStore.volumeLimits.max_db"
                :step="1"
                show-value
                value-unit=" dB"
                :disabled="getClientMute(client.id) || !client.available || disabled"
                @input="(v) => handleVolumeInput(client.id, v)"
                @change="(v) => handleVolumeChange(client.id, v)"
              />
            </div>
            <Toggle
              :model-value="!getClientMute(client.id)"
              variant="secondary"
              @change="(enabled) => handleMuteToggle(client.id, !enabled)"
            />
            </div>
          </template>
        </div>
      </template>
    </section>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue';
import { useDspStore } from '@/stores/dspStore';
import { useSettingsStore } from '@/stores/settingsStore';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import Button from '@/components/ui/Button.vue';
import RangeSlider from '@/components/ui/RangeSlider.vue';
import Toggle from '@/components/ui/Toggle.vue';
import SvgIcon from '@/components/ui/SvgIcon.vue';

const dspStore = useDspStore();
const settingsStore = useSettingsStore();
const audioStore = useUnifiedAudioStore();

const props = defineProps({
  disabled: {
    type: Boolean,
    default: false
  }
});

const emit = defineEmits(['targetChange', 'configureZone']);

// Local state
const selectedTargetLocal = ref(dspStore.selectedTarget);

// Throttling for volume updates
const volumeThrottleMap = new Map();

// === COMPUTED ===
const targets = computed(() => dspStore.availableTargets);

// Convert targets to tabs format (zones + individual clients)
const zoneTabs = computed(() => {
  const tabs = [];
  const multiroomEnabled = audioStore.systemState.multiroom_enabled;

  // When multiroom is disabled, only show local Milo
  if (!multiroomEnabled) {
    const localTarget = targets.value.find(t => t.id === 'local');
    if (localTarget) {
      return [{
        label: localTarget.name,
        value: 'local',
        disabled: !localTarget.available
      }];
    }
    return [];
  }

  // Group linked clients into zones (multiroom enabled)
  const processedIds = new Set();

  for (const target of targets.value) {
    if (processedIds.has(target.id)) continue;

    const linkedIds = dspStore.getLinkedClientIds(target.id);

    if (linkedIds.length > 1) {
      // This is a zone - get names of linked clients (local first)
      const linkedClients = dspStore.sortClientIdsLocalFirst(linkedIds)
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

// Get current zone name
const currentZoneName = computed(() => {
  const tab = zoneTabs.value.find(t => t.value === selectedTargetLocal.value);
  return tab ? tab.label : '';
});

// Get current zone group ID (for configure button)
const currentZoneGroupId = computed(() => {
  const tab = zoneTabs.value.find(t => t.value === selectedTargetLocal.value);
  return tab?.groupId || null;
});

// Get clients in the selected zone
const zoneClients = computed(() => {
  if (!isZoneSelected.value) {
    // Single client - return it
    const target = targets.value.find(t => t.id === selectedTargetLocal.value);
    return target ? [target] : [];
  }

  // Parse zone client IDs (local first)
  const clientIds = selectedTargetLocal.value.replace('zone:', '').split(',');
  return dspStore.sortClientIdsLocalFirst(clientIds)
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

// Selected client IDs (for level meters aggregation)
const selectedClientIds = computed(() => {
  if (isZoneSelected.value) {
    return selectedTargetLocal.value.replace('zone:', '').split(',');
  }
  return [selectedTargetLocal.value];
});

// === VOLUME HELPERS ===
function getClientVolume(clientId) {
  return Math.round(dspStore.getClientDspVolume(clientId));
}

function getClientMute(clientId) {
  return dspStore.getClientDspMute(clientId);
}

// Get speaker icon name based on type
function getSpeakerIcon(clientDspId) {
  const speakerType = dspStore.getClientSpeakerType(clientDspId);
  const iconMap = {
    satellite: 'speakerSatellite',
    bookshelf: 'speakerShelf',
    tower: 'speakerColumn',
    subwoofer: 'speakerSub'
  };
  return iconMap[speakerType] || 'speakerShelf';
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

function handleConfigureZone() {
  emit('configureZone', currentZoneGroupId.value);
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
  // Mute only this specific client, not all zone members
  await dspStore.updateClientDspMute(clientId, muted);
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

// Expose selectedZoneName and selectedClientIds for parent components
defineExpose({ selectedZoneName, selectedClientIds });
</script>

<style scoped>
.item-selector {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.settings-section {
  background: var(--color-background-neutral);
  border-radius: var(--radius-06);
  padding: var(--space-05-fixed) var(--space-05);
}
.settings-section.tabs-section{
  border-radius: var(--radius-05);
}

/* === TABS SECTION === */
.tabs-section {
  padding: var(--space-02);
}

.tabs-container {
  display: flex;
  gap: var(--space-02);
  overflow-x: auto;
}

.tabs-container::-webkit-scrollbar {
  height: 4px;
}

.tabs-container::-webkit-scrollbar-thumb {
  background: var(--color-border);
  border-radius: 2px;
}

.tab-button {
  flex: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-02);
  height: 40px;
  padding: 10px 16px;
  border: none;
  border-radius: var(--radius-03);
  cursor: pointer;
  white-space: nowrap;
  transition: background-color var(--transition-fast), color var(--transition-fast);
  /* Inactive state - outline */
  background-color: var(--color-background-neutral);
  color: var(--color-brand);
  box-shadow: inset 0 0 0 2px var(--color-brand);
}

.tab-button--active {
  background-color: var(--color-brand);
  color: var(--color-text-contrast);
  box-shadow: none;
}

.tab-button--disabled {
  background-color: var(--color-background);
  color: var(--color-text-light);
  box-shadow: none;
  cursor: not-allowed;
}

.tab-badge {
  opacity: 0.8;
}

/* === VOLUME SECTION === */
.volume-section {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

/* Single client layout (CAS 1) - horizontal row */
.single-client-row {
  display: grid;
  grid-template-columns: 40px auto 1fr auto;
  align-items: center;
  gap: var(--space-03);
}

.client-icon-container {
  width: 40px;
  height: 40px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--color-background-strong);
  border-radius: var(--radius-03);
  flex-shrink: 0;
  color: var(--color-text-secondary);
}

.client-info {
  display: flex;
  flex-direction: column;
  min-width: 140px;
}

.client-info .client-name {
  color: var(--color-text);
}

.client-info .volume-label {
  color: var(--color-text-secondary);
}

/* Zone layout (CAS 2 & 3) */
.zone-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-03);
}

.zone-header__info {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
}

.volume-label {
  color: var(--color-text-secondary);
}

/* Clients list */
.clients-list {
  display: flex;
  flex-direction: column;
}

/* Separator between clients */
.client-separator {
  height: 1px;
  background: var(--color-border);
  margin: var(--space-04) 0;
}

.client-separator:first-child {
  margin-top: 0;
}

.client-volume-row {
  display: grid;
  grid-template-columns: 40px auto 1fr auto;
  align-items: center;
  gap: var(--space-03);
}

.client-volume-row .client-name {
  color: var(--color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  transition: color var(--transition-fast);
  min-width: 130px;
}

.client-volume-row .client-name.muted,
.client-volume-row .client-name.offline {
  color: var(--color-text-light);
}

.volume-control {
  min-width: 0;
}

/* Offline badge */
.badge-offline {
  display: inline-block;
  background: var(--color-background-medium);
  color: var(--color-text-light);
  padding: 1px 6px;
  border-radius: var(--radius-02);
  font-size: 10px;
  font-weight: 500;
  text-transform: uppercase;
  margin-left: var(--space-02);
  vertical-align: middle;
}

/* Mobile adjustments */
@media (max-aspect-ratio: 4/3) {
  .settings-section {
    border-radius: var(--radius-05);
  }

  .tabs-container {
    gap: var(--space-01);
  }

  .tab-button {
    height: 38px;
    padding: 8px 14px;
  }

  .client-volume-row {
    display: grid;
    grid-template-columns: 40px 1fr auto;
    grid-template-rows: auto auto;
    gap: var(--space-02);
  }

  .client-volume-row .client-icon-container {
    grid-column: 1;
    grid-row: 1;
  }

  .client-volume-row .client-name {
    grid-column: 2;
    grid-row: 1;
    align-self: center;
  }

  .client-volume-row > :deep(.toggle) {
    grid-column: 3;
    grid-row: 1;
  }

  .client-volume-row .volume-control {
    grid-column: 1 / -1;
    grid-row: 2;
  }

  /* Single client mobile - same grid pattern */
  .single-client-row {
    display: grid;
    grid-template-columns: 40px 1fr auto;
    grid-template-rows: auto auto;
    gap: var(--space-02);
  }

  .single-client-row .client-icon-container {
    grid-column: 1;
    grid-row: 1;
  }

  .single-client-row .client-info {
    grid-column: 2;
    grid-row: 1;
    align-self: center;
  }

  .single-client-row > :deep(.toggle) {
    grid-column: 3;
    grid-row: 1;
  }

  .single-client-row .volume-control {
    grid-column: 1 / -1;
    grid-row: 2;
  }
}
</style>
