<!-- frontend/src/components/settings/categories/multiroom/MultiroomSettings.vue -->
<template>
  <Transition name="fade-slide" mode="out-in">
        <!-- MESSAGE: Enabling or Disabled -->
        <MessageContent v-if="showMessage" :key="transitionState" :loading="isLoading" :loading-delay="0"
          :icon="isLoading ? null : 'multiroom'" :title="messageTitle" />
        <!-- SETTINGS: Active and ready -->
        <SettingsContainer v-else key="settings">
          <!-- Zones & Speakers Section -->
          <SettingsSection>
            <template #header>
              <SectionHeader :title="t('multiroom.zonesAndSpeakers')">
                <template #actions>
                  <Button v-if="ungroupedClients.length >= 2" variant="brand" size="small" @click="handleCreateZone">
                    {{ t('dsp.zones.createZone', 'Create Zone') }}
                  </Button>
                </template>
              </SectionHeader>
            </template>

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
                <button type="button" class="zone-header" @click="handleEditZone(zone.id)">
                  <span class="zone-header__name heading-3">{{ zone.displayName }}</span>
                  <SvgIcon name="caretRight" :size="20" class="zone-header__caret" />
                  <!-- Crossover badge -->
                  <span v-if="zone.crossover_enabled" class="crossover-badge crossover-badge--active text-mono"
                    :title="t('multiroom.crossover.badgeActive')">
                    {{ zone.crossover_frequency}} Hz
                  </span>
                  <span v-else-if="zone.has_subwoofer" class="crossover-badge crossover-badge--inactive text-mono"
                    :title="t('multiroom.crossover.subwooferOffline')">
                    {{ t('multiroom.crossover.badgeInactive') }}
                  </span>
                </button>
                <!-- Zone clients -->
                <div class="zone-clients">
                  <SpeakerListItem v-for="client in zone.clients" :key="client.id"
                    :name="client.name" :mac-id="client.mac_id" :online="client.online"
                    @click="handleEditClient(client.mac_id)" />
                </div>
              </div>

              <!-- Individual speakers section -->
              <template v-if="ungroupedClients.length > 0">
                <h3 v-if="zones.length > 0" class="heading-3 section-subtitle">{{ t('multiroom.individualSpeakers') }}
                </h3>
                <div class="ungrouped-clients">
                  <SpeakerListItem v-for="client in ungroupedClients" :key="client.id"
                    :name="client.name" :mac-id="client.mac_id" :online="client.online"
                    @click="handleEditClient(client.mac_id)" />
                </div>
              </template>
            </div>
          </SettingsSection>

          <!-- Advanced settings (includes presets) -->
          <SettingsSection :title="t('multiroomSettings.presets')">
            <ButtonGroup :model-value="activePresetId" :options="presetOptions"
              :disabled="snapcastStore.isApplyingServerConfig" mobile-layout="column" @change="handlePresetChange" />

            <div class="section-divider"></div>

            <h2 class="heading-2">{{ t('multiroomSettings.advanced') }}</h2>

            <SettingItem :label="t('multiroomSettings.globalBuffer')">
              <RangeSlider v-model="snapcastStore.serverConfig.buffer" :min="100" :max="2000" :step="50"
                value-unit="ms" />
            </SettingItem>

            <SettingItem :label="t('multiroomSettings.chunkSize')">
              <RangeSlider v-model="snapcastStore.serverConfig.chunk_ms" :min="10" :max="100" :step="5"
                value-unit="ms" />
            </SettingItem>

            <SettingItem :label="t('multiroomSettings.codec')">
              <ButtonGroup :model-value="snapcastStore.serverConfig.codec" :options="codecOptions"
                mobile-layout="column" @change="selectCodec" />
            </SettingItem>
          </SettingsSection>

          <Button v-if="snapcastStore.hasServerConfigChanges" variant="brand" size="medium" class="apply-button-sticky"
            :disabled="snapcastStore.isApplyingServerConfig" @click="applyServerConfig">
            {{ snapcastStore.isApplyingServerConfig ? t('multiroom.restarting') : t('multiroomSettings.apply') }}
          </Button>
        </SettingsContainer>
  </Transition>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue';
import { useI18n } from '@/services/i18n';
import useWebSocket from '@/services/websocket';
import { useSnapcastStore } from '@/stores/snapcastStore';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useMultiroomStore } from '@/stores/multiroomStore';
import Button from '@/components/ui/Button.vue';
import ButtonGroup from '@/components/ui/ButtonGroup.vue';
import RangeSlider from '@/components/ui/RangeSlider.vue';
import SpeakerListItem from '@/components/settings/categories/multiroom/SpeakerListItem.vue';
import MessageContent from '@/components/ui/MessageContent.vue';
import SvgIcon from '@/components/ui/SvgIcon.vue';
import SettingsContainer from '@/components/settings/SettingsContainer.vue';
import SettingsSection from '@/components/settings/SettingsSection.vue';
import SectionHeader from '@/components/settings/SectionHeader.vue';
import SettingItem from '@/components/settings/SettingItem.vue';

const emit = defineEmits(['edit-zone', 'create-zone', 'edit-client']);

const { t } = useI18n();
const { on } = useWebSocket();
const snapcastStore = useSnapcastStore();
const unifiedStore = useUnifiedAudioStore();
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

    return {
      id: zone.id,
      displayName: zone.name || `Zone ${index + 1}`,
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

.zone-header__caret {
  color: var(--color-brand);
}

/* Crossover badge */
.crossover-badge {
  display: inline-flex;
  align-items: center;
  margin-left: auto;
  padding: var(--space-01) var(--space-02);
  border-radius: var(--radius-02);
  white-space: nowrap;
}

.crossover-badge--active {
  background: var(--color-background);
  color: var(--color-text-secondary);
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

.apply-button-sticky {
  position: sticky;
  bottom: 0;
  width: 100%;
  z-index: 10;
}

/* Responsive */
@media (max-aspect-ratio: 4/3) {
  .zone-clients,
  .ungrouped-clients {
    grid-template-columns: 1fr;
  }
}
</style>
