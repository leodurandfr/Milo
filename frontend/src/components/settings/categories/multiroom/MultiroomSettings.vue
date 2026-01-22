<!-- frontend/src/components/settings/categories/multiroom/MultiroomSettings.vue -->
<template>
  <div class="multiroom-settings">
    <div class="content-wrapper">
      <Transition name="fade-slide" mode="out-in">
        <!-- MESSAGE: Enabling or Disabled -->
        <MessageContent
          v-if="showMessage"
          :key="transitionState"
          :loading="isLoading"
          :loading-delay="0"
          :icon="isLoading ? null : 'multiroom'"
          :title="messageTitle"
        />
        <!-- SETTINGS: Active and ready -->
        <div v-else key="settings" class="settings-container">
            <!-- Zones & Speakers Section -->
            <section class="settings-section">
              <div class="multiroom-group" :class="{ 'multiroom-group--compact': ungroupedClients.length >= 2 }">
                <!-- Header: Title + Create Zone Button -->
                <div class="section-header">
                  <h2 class="heading-2">{{ t('multiroom.zonesAndSpeakers') }}</h2>
                  <Button
                    v-if="ungroupedClients.length >= 2"
                    variant="brand"
                    size="small"
                    @click="handleCreateZone"
                  >
                    {{ t('dsp.zones.createZone', 'Create Zone') }}
                  </Button>
                </div>

                <div v-if="snapcastStore.isLoading" class="loading-state">
                  <p class="text-mono">{{ t('multiroom.loadingSpeakers') }}</p>
                </div>

                <div v-else-if="sortedMultiroomClients.length === 0" class="no-clients-state">
                  <p class="text-mono">{{ t('multiroom.noSpeakers') }}</p>
                </div>

                <div v-else class="speakers-list">
                  <!-- Zones -->
                  <div v-for="zone in zones" :key="zone.id" class="zone-group">
                    <!-- Zone header (clickable) -->
                    <button
                      type="button"
                      class="zone-header"
                      @click="handleEditZone(zone.id)"
                    >
                      <span class="zone-header__name heading-3">{{ zone.displayName }}</span>
                      <!-- Crossover badge -->
                      <span
                        v-if="zone.crossover_enabled"
                        class="crossover-badge crossover-badge--active"
                        :title="t('multiroom.crossover.badgeActive')"
                      >
                        {{ zone.crossover_frequency || 80 }} Hz
                      </span>
                      <span
                        v-else-if="zone.has_subwoofer"
                        class="crossover-badge crossover-badge--inactive"
                        :title="t('multiroom.crossover.subwooferOffline')"
                      >
                        {{ t('multiroom.crossover.badgeInactive') }}
                      </span>
                      <span class="zone-header__count text-mono-small">{{ zone.onlineCount }}/{{ zone.clientCount }}</span>
                      <SvgIcon name="caretRight" :size="20" class="zone-header__caret" />
                    </button>
                    <!-- Zone clients -->
                    <div class="zone-clients">
                      <ListItemButton
                        v-for="client in zone.clients"
                        :key="client.id"
                        variant="background"
                        icon-variant="standard"
                        action="caret"
                        @click="handleEditClient(client.mac_id)"
                      >
                        <template #icon>
                          <div class="client-icon" :class="{ 'is-offline': !client.online }">
                            <SvgIcon :name="getSpeakerIcon(client.mac_id)" :size="28" />
                          </div>
                        </template>
                        <template #title>
                          <div class="client-title">
                            <span>{{ client.name }}</span>
                            <span class="text-mono-small client-title__type">{{ getSpeakerTypeLabel(client.mac_id) }}</span>
                          </div>
                        </template>
                      </ListItemButton>
                    </div>
                  </div>

                  <!-- Individual speakers section -->
                  <template v-if="ungroupedClients.length > 0">
                    <h3 v-if="zones.length > 0" class="heading-3 section-subtitle">{{ t('multiroom.individualSpeakers') }}</h3>
                    <div class="ungrouped-clients">
                      <ListItemButton
                        v-for="client in ungroupedClients"
                        :key="client.id"
                        variant="background"
                        icon-variant="standard"
                        action="caret"
                        @click="handleEditClient(client.mac_id)"
                      >
                        <template #icon>
                          <div class="client-icon" :class="{ 'is-offline': !client.online }">
                            <SvgIcon :name="getSpeakerIcon(client.mac_id)" :size="28" />
                          </div>
                        </template>
                        <template #title>
                          <div class="client-title">
                            <span>{{ client.name }}</span>
                            <span class="text-mono-small client-title__type">{{ getSpeakerTypeLabel(client.mac_id) }}</span>
                          </div>
                        </template>
                      </ListItemButton>
                    </div>
                  </template>
                </div>
              </div>
            </section>

            <!-- Advanced settings (includes presets) -->
            <section class="settings-section">
              <div class="multiroom-group">
                <!-- Presets -->
                <h2 class="heading-2">{{ t('multiroomSettings.presets') }}</h2>
                <ButtonGroup
                  :model-value="activePresetId"
                  :options="presetOptions"
                  :disabled="snapcastStore.isApplyingServerConfig"
                  mobile-layout="column"
                  @change="handlePresetChange"
                />

                <div class="section-divider"></div>

                <!-- Advanced controls -->
                <h2 class="heading-2">{{ t('multiroomSettings.advanced') }}</h2>

                <div class="form-group">
                  <label class="text-mono">{{ t('multiroomSettings.globalBuffer') }}</label>
                  <RangeSlider v-model="snapcastStore.serverConfig.buffer" :min="100" :max="2000" :step="50"
                    value-unit="ms" />
                </div>

                <div class="form-group">
                  <label class="text-mono">{{ t('multiroomSettings.chunkSize') }}</label>
                  <RangeSlider v-model="snapcastStore.serverConfig.chunk_ms" :min="10" :max="100" :step="5"
                    value-unit="ms" />
                </div>

                <div class="form-group">
                  <label class="text-mono">{{ t('multiroomSettings.codec') }}</label>
                  <ButtonGroup
                    :model-value="snapcastStore.serverConfig.codec"
                    :options="codecOptions"
                    mobile-layout="column"
                    @change="selectCodec"
                  />
                </div>
              </div>
            </section>

            <Button v-if="snapcastStore.hasServerConfigChanges" variant="brand" size="medium" class="apply-button-sticky"
              :disabled="snapcastStore.isApplyingServerConfig" @click="applyServerConfig">
              {{ snapcastStore.isApplyingServerConfig ? t('multiroom.restarting') : t('multiroomSettings.apply') }}
           </Button>
          </div>
        </Transition>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue';
import { useI18n } from '@/services/i18n';
import useWebSocket from '@/services/websocket';
import { useSnapcastStore } from '@/stores/snapcastStore';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useDspStore } from '@/stores/dspStore';
import { useMultiroomStore } from '@/stores/multiroomStore';
import Button from '@/components/ui/Button.vue';
import ButtonGroup from '@/components/ui/ButtonGroup.vue';
import RangeSlider from '@/components/ui/RangeSlider.vue';
import ListItemButton from '@/components/ui/ListItemButton.vue';
import MessageContent from '@/components/ui/MessageContent.vue';
import SvgIcon from '@/components/ui/SvgIcon.vue';

const emit = defineEmits(['edit-zone', 'create-zone', 'edit-client']);

const { t } = useI18n();
const { on } = useWebSocket();
const snapcastStore = useSnapcastStore();
const unifiedStore = useUnifiedAudioStore();
const dspStore = useDspStore();
const multiroomClientStore = useMultiroomStore();

// Multiroom state
const isMultiroomActive = computed(() => unifiedStore.systemState.multiroom_enabled);
const transitionState = ref('idle'); // 'idle' | 'enabling' | 'disabling'

// Message display logic
const showMessage = computed(() => {
  return transitionState.value !== 'idle' || !isMultiroomActive.value;
});
const isLoading = computed(() => transitionState.value === 'enabling');
const messageTitle = computed(() => {
  return transitionState.value === 'enabling' ? t('multiroom.starting') : t('multiroom.disabled');
});

// Clients are already sorted (local first, then alphabetical) from multiroomStore
const sortedMultiroomClients = computed(() => snapcastStore.clients);

// Get zones with client details from multiroomStore (single source of truth)
// Uses clientList which is already sorted (local first, online first, alphabetical)
const zones = computed(() => {
  return multiroomClientStore.zoneList.map((zone, index) => {
    const zoneClientIds = new Set(zone.client_ids || []);
    // Filter from already-sorted clientList to preserve correct order
    const clients = multiroomClientStore.clientList
      .filter(c => zoneClientIds.has(c.mac_id))
      .map(client => ({
        id: client.id,
        mac_id: client.mac_id,
        host: client.host,
        name: client.name || client.host,
        online: client.online
      }));

    const onlineCount = clients.filter(c => c.online).length;

    return {
      id: zone.id,
      displayName: zone.name || `Zone ${index + 1}`,
      clientCount: clients.length,
      onlineCount,
      clients,
      crossover_enabled: zone.crossover_enabled,
      crossover_frequency: zone.crossover_frequency,
      has_subwoofer: zone.has_subwoofer
    };
  });
});

// Get clients not in any zone from multiroomStore (single source of truth)
const ungroupedClients = computed(() => {
  const groupedIds = new Set();
  multiroomClientStore.zoneList.forEach(zone => {
    (zone.client_ids || []).forEach(id => groupedIds.add(id));
  });

  return multiroomClientStore.clientList
    .filter(client => !groupedIds.has(client.mac_id))
    .map(client => ({
      id: client.id,
      mac_id: client.mac_id,
      host: client.host,
      name: client.name || client.host,
      online: client.online
    }));
});

// Get translated speaker type label
function getSpeakerTypeLabel(clientMacId) {
  const speakerType = dspStore.getClientSpeakerType(clientMacId);
  return t(`multiroom.speakerTypes.${speakerType}`);
}

// Get speaker icon name based on type
function getSpeakerIcon(clientMacId) {
  const speakerType = dspStore.getClientSpeakerType(clientMacId);
  const iconMap = {
    satellite: 'speakerSatellite',
    bookshelf: 'speakerShelf',
    tower: 'speakerColumn',
    subwoofer: 'speakerSub'
  };
  return iconMap[speakerType] || 'speakerShelf';
}

// Get crossover settings for a zone
function getZoneCrossover(zoneId) {
  return dspStore.getZoneCrossoverSettings(zoneId);
}

// Check if zone has a subwoofer (online or offline)
function zoneHasSubwoofer(zone) {
  return zone.clients?.some(c => dspStore.getClientSpeakerType(c.mac_id) === 'subwoofer');
}

// Navigation handlers - emit to parent (SettingsModal)
function handleEditZone(groupId) {
  emit('edit-zone', groupId);
}

function handleCreateZone() {
  emit('create-zone');
}

function handleEditClient(macId) {
  emit('edit-client', macId);
}

const audioPresets = computed(() => [
  {
    id: 'reactivity',
    name: t('multiroomSettings.reactivity'),
    config: { buffer: 150, codec: 'opus', chunk_ms: 10 }
  },
  {
    id: 'balanced',
    name: t('multiroomSettings.balanced'),
    config: { buffer: 1000, codec: 'opus', chunk_ms: 20 }
  },
  {
    id: 'quality',
    name: t('multiroomSettings.optimalQuality'),
    config: { buffer: 1500, codec: 'flac', chunk_ms: 40 }
  }
]);

// ButtonGroup options for presets
const presetOptions = computed(() =>
  audioPresets.value.map(preset => ({
    label: preset.name,
    value: preset.id
  }))
);

// Active preset ID (or null if custom config)
const activePresetId = computed(() => {
  const current = snapcastStore.serverConfig;
  const active = audioPresets.value.find(preset =>
    current.buffer === preset.config.buffer &&
    current.codec === preset.config.codec &&
    current.chunk_ms === preset.config.chunk_ms
  );
  return active?.id || null;
});

// Codec options for ButtonGroup
const codecOptions = [
  { label: 'Opus', value: 'opus' },
  { label: 'FLAC', value: 'flac' },
  { label: 'PCM', value: 'pcm' }
];

// === MULTIROOM - CLIENTS ===

async function loadMultiroomData() {
  // Load clients and server config from the store
  // Zone/client data comes from multiroomStore (initialized in App.vue)
  await Promise.all([
    snapcastStore.loadClients(),
    snapcastStore.loadServerConfig()
  ]);
  // Volume data comes from unifiedAudioStore.volumeState via WebSocket
}

// === MULTIROOM - SERVER CONFIG ===

function handlePresetChange(presetId) {
  const preset = audioPresets.value.find(p => p.id === presetId);
  if (preset) {
    snapcastStore.applyPreset(preset);
  }
}

function selectCodec(codecName) {
  snapcastStore.selectCodec(codecName);
}

async function applyServerConfig() {
  await snapcastStore.applyServerConfig();
}

onMounted(async () => {
  // Load only if multiroom is enabled
  if (isMultiroomActive.value) {
    await loadMultiroomData();
  }

  // Handle multiroom state transitions
  on('routing', 'multiroom_enabling', () => {
    transitionState.value = 'enabling';
  });

  on('routing', 'multiroom_disabling', () => {
    transitionState.value = 'disabling';
  });

  on('routing', 'multiroom_ready', async () => {
    transitionState.value = 'idle';
    await loadMultiroomData();
  });

  // Reset to idle when multiroom is fully disabled
  on('system', 'state_changed', (event) => {
    if (event?.multiroom_enabled === false && transitionState.value === 'disabling') {
      transitionState.value = 'idle';
    }
  });

  // Subscribe to volume changes - handled by unifiedAudioStore
  on('volume', 'volume_changed', (event) => {
    unifiedStore.handleVolumeEvent(event);
  });

  // Client names are synced automatically via multiroomStore (registry:client_updated events)
});
</script>

<style scoped>
.multiroom-settings {
  display: flex;
  flex-direction: column;
}

.content-wrapper {
  display: flex;
  flex-direction: column;
  border-radius: var(--radius-07);
  transition: background 400ms ease;
}

.content-wrapper.with-background {
  background: var(--color-background-neutral);
}

.settings-container {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.settings-section {
  background: var(--color-background-neutral);
  border-radius: var(--radius-06);
  padding: var(--space-05-fixed) var(--space-05);
  display: flex;
  flex-direction: column;
  gap: var(--space-05-fixed);
}

.multiroom-group {
  display: flex;
  flex-direction: column;
  gap: var(--space-05);
}

.multiroom-group--compact {
  gap: var(--space-04);
}

.section-divider {
  height: 1px;
  background: var(--color-border);
}

.loading-state,
.no-clients-state {
  text-align: center;
  padding: var(--space-04);
  color: var(--color-text-secondary);
}

/* Section header with title and button */
.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-03);
}

/* Speakers list */
.speakers-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

/* Zone group (zone header + clients) */
.zone-group {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

/* Zone clients */
.zone-clients {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-01);
}

/* Ungrouped clients grid */
.ungrouped-clients {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-01);
}

/* Client title with name and type stacked */
.client-title {
  display: flex;
  flex-direction: column;
}

.client-title__type {
  color: var(--color-text-secondary);
}

/* Client icon with online/offline indicator */
.client-icon {
  display: flex;
  align-items: center;
  justify-content: center;
}

.client-icon.is-offline {
  opacity: 0.4;
}

/* Zone header button */
.zone-header {
  display: flex;
  align-items: flex-end;
  gap: var(--space-01);
  width: 100%;
  cursor: pointer;
}

.zone-header__name {
  color: var(--color-brand);
}

.zone-header__count {
  color: var(--color-text-secondary);
  margin-left: auto;
  margin-right: var(--space-01);
}

.zone-header__caret {
  flex-shrink: 0;
  color: var(--color-brand);
}

/* Crossover badge */
.crossover-badge {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: var(--font-size-small);
  font-family: var(--font-family-mono);
  white-space: nowrap;
}

.crossover-badge--active {
  background: var(--color-success-subtle, rgba(34, 197, 94, 0.15));
  color: var(--color-success, #22c55e);
}

.crossover-badge--inactive {
  background: var(--color-warning-subtle, rgba(234, 179, 8, 0.15));
  color: var(--color-warning, #eab308);
  opacity: 0.8;
}

/* Section subtitle (e.g., "Individual speakers") */
.section-subtitle {
  color: var(--color-text-secondary);
  margin-top: var(--space-03);
  margin-bottom: var(--space-01);
}

.form-group {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.form-group label {
  color: var(--color-text-secondary);
}

.apply-button-sticky {
  position: sticky;
  bottom: 0;
  width: 100%;
  z-index: 10;
}

/* Responsive */
@media (max-aspect-ratio: 4/3) {
  .settings-section {
    border-radius: var(--radius-05);
  }

  .zone-clients,
  .ungrouped-clients {
    grid-template-columns: 1fr;
  }
}
</style>
