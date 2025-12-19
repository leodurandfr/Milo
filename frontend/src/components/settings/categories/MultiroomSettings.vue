<!-- frontend/src/components/settings/categories/MultiroomSettings.vue -->
<template>
  <div class="multiroom-settings">
    <div class="content-wrapper" :class="{ 'with-background': !isMultiroomActive }">
      <!-- MESSAGE: Multiroom disabled -->
        <Transition name="fade-slide">
          <MessageContent v-if="!isMultiroomActive" key="message" icon="multiroom" :title="t('multiroom.disabled')" />
        </Transition>

        <!-- SETTINGS: Sections visible only if multiroom is enabled -->
        <Transition name="settings">
          <div v-if="isMultiroomActive" key="settings" class="settings-container">
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

                <div v-if="multiroomStore.isLoading" class="loading-state">
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
                      <span class="zone-header__label heading-3">Zone</span>
                      <span class="zone-header__separator heading-3">·</span>
                      <span class="zone-header__name heading-3">{{ zone.displayName }}</span>
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
                        @click="handleEditClient(client.id)"
                      >
                        <template #icon>
                          <SvgIcon :name="getSpeakerIcon(client.dsp_id)" :size="28" />
                        </template>
                        <template #title>
                          <div class="client-title">
                            <span>{{ client.name }}</span>
                            <span class="text-mono-small client-title__type">{{ getSpeakerTypeLabel(client.dsp_id) }}</span>
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
                        @click="handleEditClient(client.id)"
                      >
                        <template #icon>
                          <SvgIcon :name="getSpeakerIcon(client.dsp_id)" :size="28" />
                        </template>
                        <template #title>
                          <div class="client-title">
                            <span>{{ client.name }}</span>
                            <span class="text-mono-small client-title__type">{{ getSpeakerTypeLabel(client.dsp_id) }}</span>
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
                  :disabled="multiroomStore.isApplyingServerConfig"
                  mobile-layout="column"
                  @change="handlePresetChange"
                />

                <div class="section-divider"></div>

                <!-- Advanced controls -->
                <h2 class="heading-2">{{ t('multiroomSettings.advanced') }}</h2>

                <div class="form-group">
                  <label class="text-mono">{{ t('multiroomSettings.globalBuffer') }}</label>
                  <RangeSlider v-model="multiroomStore.serverConfig.buffer" :min="100" :max="2000" :step="50"
                    value-unit="ms" />
                </div>

                <div class="form-group">
                  <label class="text-mono">{{ t('multiroomSettings.chunkSize') }}</label>
                  <RangeSlider v-model="multiroomStore.serverConfig.chunk_ms" :min="10" :max="100" :step="5"
                    value-unit="ms" />
                </div>

                <div class="form-group">
                  <label class="text-mono">{{ t('multiroomSettings.codec') }}</label>
                  <ButtonGroup
                    :model-value="multiroomStore.serverConfig.codec"
                    :options="codecOptions"
                    mobile-layout="column"
                    @change="selectCodec"
                  />
                </div>
              </div>
            </section>

            <Button v-if="multiroomStore.hasServerConfigChanges" variant="brand" size="medium" class="apply-button-sticky"
              :disabled="multiroomStore.isApplyingServerConfig" @click="applyServerConfig">
              {{ multiroomStore.isApplyingServerConfig ? t('multiroom.restarting') : t('multiroomSettings.apply') }}
           </Button>
          </div>
        </Transition>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, watch } from 'vue';
import { useI18n } from '@/services/i18n';
import useWebSocket from '@/services/websocket';
import { useMultiroomStore } from '@/stores/multiroomStore';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useDspStore } from '@/stores/dspStore';
import Button from '@/components/ui/Button.vue';
import ButtonGroup from '@/components/ui/ButtonGroup.vue';
import RangeSlider from '@/components/ui/RangeSlider.vue';
import ListItemButton from '@/components/ui/ListItemButton.vue';
import MessageContent from '@/components/ui/MessageContent.vue';
import SvgIcon from '@/components/ui/SvgIcon.vue';

const emit = defineEmits(['edit-zone', 'create-zone', 'edit-client']);

const { t } = useI18n();
const { on } = useWebSocket();
const multiroomStore = useMultiroomStore();
const unifiedStore = useUnifiedAudioStore();
const dspStore = useDspStore();

// Multiroom state
const isMultiroomActive = computed(() => unifiedStore.systemState.multiroom_enabled);

// Sorted clients with "milo" first
const sortedMultiroomClients = computed(() => multiroomStore.sortedClients);

// Get zones with client details
const zones = computed(() => {
  return dspStore.linkedGroups.map((group, index) => {
    const clients = dspStore.sortClientIdsLocalFirst(group.client_ids || [])
      .map(dspId => {
        const client = multiroomStore.clients.find(c => c.dsp_id === dspId);
        return client ? {
          id: client.id,
          dsp_id: dspId,
          host: client.host,
          name: client.name || client.host
        } : null;
      })
      .filter(Boolean);

    return {
      id: group.id,
      displayName: group.name || `Zone ${index + 1}`,
      clientCount: clients.length,
      clients
    };
  });
});

// Get clients not in any zone
const ungroupedClients = computed(() => {
  const groupedIds = new Set();
  dspStore.linkedGroups.forEach(group => {
    (group.client_ids || []).forEach(id => groupedIds.add(id));
  });

  return multiroomStore.clients
    .filter(client => !groupedIds.has(client.dsp_id))
    .map(client => ({
      id: client.id,
      dsp_id: client.dsp_id,
      host: client.host,
      name: client.name || client.host
    }));
});

// Get translated speaker type label
function getSpeakerTypeLabel(clientDspId) {
  const speakerType = dspStore.getClientSpeakerType(clientDspId);
  return t(`multiroom.speakerTypes.${speakerType}`);
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

// Navigation handlers - emit to parent (SettingsModal)
function handleEditZone(groupId) {
  emit('edit-zone', groupId);
}

function handleCreateZone() {
  emit('create-zone');
}

function handleEditClient(clientId) {
  emit('edit-client', clientId);
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
  const current = multiroomStore.serverConfig;
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
  await Promise.all([
    multiroomStore.loadClients(),
    multiroomStore.loadServerConfig(),
    dspStore.loadTargets() // Load DSP targets for linkedGroups
  ]);

  // Load DSP volumes for all clients
  await dspStore.loadAllClientDspVolumes(multiroomStore.clients);
}

// === MULTIROOM - SERVER CONFIG ===

function handlePresetChange(presetId) {
  const preset = audioPresets.value.find(p => p.id === presetId);
  if (preset) {
    multiroomStore.applyPreset(preset);
  }
}

function selectCodec(codecName) {
  multiroomStore.selectCodec(codecName);
}

async function applyServerConfig() {
  await multiroomStore.applyServerConfig();
}

// Watcher to load data when multiroom is enabled
watch(isMultiroomActive, async (newValue, oldValue) => {
  if (newValue && !oldValue) {
    // Multiroom has just been enabled, load data
    await loadMultiroomData();
  }
});

onMounted(async () => {
  // Load only if multiroom is enabled
  if (isMultiroomActive.value) {
    await loadMultiroomData();
  }

  // Subscribe to WebSocket events for linked groups changes
  on('dsp', 'links_changed', (e) => dspStore.handleLinksChanged(e));

  // Subscribe to volume changes from other UIs
  on('volume', 'volume_changed', (event) => {
    if (event.data?.client_volumes) {
      for (const [hostname, volumeDb] of Object.entries(event.data.client_volumes)) {
        dspStore.setClientDspVolume(hostname, volumeDb);
      }
    }
  });

  // Subscribe to DSP client volumes pushed (when multiroom activates)
  on('dsp', 'client_volumes_pushed', (e) => dspStore.handleClientVolumesPushed(e));

  // Keep client names in sync for ZoneEdit
  on('snapcast', 'client_name_changed', (e) => dspStore.handleClientNameChanged(e));
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

/* Zone header button */
.zone-header {
  display: flex;
  align-items: flex-end;
  gap: var(--space-01);
  width: 100%;
  cursor: pointer;
}

.zone-header__label {
  color: var(--color-text);
}

.zone-header__separator {
  color: var(--color-text-secondary);
}

.zone-header__name {
  color: var(--color-brand);
}

.zone-header__caret {
  flex-shrink: 0;
  color: var(--color-brand);
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

/* Transitions for settings */
.settings-enter-active {
  transition: opacity 300ms ease 100ms, transform 300ms ease 100ms;
}

.settings-leave-active {
  transition: opacity 300ms ease, transform 300ms ease;
  position: absolute;
  width: 100%;
  top: 0;
  left: 0;
}

.settings-enter-from {
  opacity: 0;
  transform: translateY(12px);
}

.settings-leave-to {
  opacity: 0;
  transform: translateY(-12px);
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
