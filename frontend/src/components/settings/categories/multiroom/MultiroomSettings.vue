<!-- frontend/src/components/settings/categories/multiroom/MultiroomSettings.vue -->
<template>
  <Transition name="fade-slide" mode="out-in">
        <!-- MESSAGE: Enabling or Disabled -->
        <MessageContent v-if="showMessage" :key="multiroomClientStore.transitionState" :loading="isLoading" :loading-delay="0"
          :icon="isLoading ? null : 'multiroom'" :title="messageTitle" />
        <!-- SETTINGS: Active and ready -->
        <SettingsContainer v-else key="settings">
          <!-- Discovered Speakers Section (pending ethernet + wifi hotspots) -->
          <SettingsSection v-if="discoveryItems.length > 0">
            <template #header>
              <SectionHeader :title="t('multiroom.pending.title')" />
            </template>
            <div class="discovery-list">
              <SystemListItem
                v-for="item in discoveryItems"
                :key="item.key"
                :name="item.name"
                :discovery-source="item.source"
                :signal="item.signal ?? null"
                :status="item.status"
                :status-variant="item.statusVariant"
                :action="item.disabled ? 'none' : 'caret'"
                :disabled="item.disabled"
                @click="handleDiscoveryClick(item)"
              />
            </div>
          </SettingsSection>

          <SettingsSection>
            <template #header>
              <SectionHeader :title="t('multiroom.zonesAndSystems')">
                <template #actions>
                  <Button v-if="ungroupedClients.length >= 2" variant="brand" size="small" @click="handleCreateZone">
                    {{ t('equalizer.zones.createZone') }}
                  </Button>
                </template>
              </SectionHeader>
            </template>

            <div v-if="snapcastStore.isLoading" class="loading-state">
              <p class="text-mono">{{ t('multiroom.loadingSystems') }}</p>
            </div>

            <div v-else-if="sortedMultiroomClients.length === 0" class="no-clients-state">
              <p class="text-mono">{{ t('multiroom.noSystems') }}</p>
            </div>

            <div v-else class="speakers-list">
              <div v-for="zone in zones" :key="zone.id" class="zone-group">
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
                <div class="zone-clients">
                  <SystemListItem v-for="client in zone.clients" :key="client.id"
                    :name="client.name" :mac-id="client.mac_id" :online="client.online"
                    @click="handleEditClient(client.mac_id)" />
                </div>
              </div>

              <template v-if="ungroupedClients.length > 0">
                <h3 v-if="zones.length > 0" class="heading-3 section-subtitle">{{ t('multiroom.individualSystems') }}
                </h3>
                <div class="ungrouped-clients">
                  <SystemListItem v-for="client in ungroupedClients" :key="client.id"
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
              <RangeSlider v-model="snapcastStore.serverConfig.buffer_ms" :min="200" :max="3000" :step="100"
                value-unit="ms" :disabled="snapcastStore.isApplyingServerConfig" />
            </SettingItem>

            <SettingItem :label="t('multiroomSettings.chunkSize')">
              <RangeSlider v-model="snapcastStore.serverConfig.chunk_ms" :min="15" :max="50" :step="5"
                value-unit="ms" :disabled="snapcastStore.isApplyingServerConfig" />
            </SettingItem>

            <SettingItem :label="t('multiroomSettings.snapclientBuffer')">
              <RangeSlider v-model="snapcastStore.serverConfig.snapclient_buffer_time" :min="60" :max="300" :step="10"
                value-unit="ms" :disabled="snapcastStore.isApplyingServerConfig" />
            </SettingItem>

            <SettingItem :label="t('multiroomSettings.codec')">
              <ButtonGroup :model-value="snapcastStore.serverConfig.codec" :options="codecOptions"
                :disabled="snapcastStore.isApplyingServerConfig" mobile-layout="column" @change="selectCodec" />
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
import { computed, onMounted, onBeforeUnmount, watch } from 'vue';
import { useI18n } from '@/services/i18n';
import { useSnapcastStore } from '@/stores/snapcastStore';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useMultiroomStore } from '@/stores/multiroomStore';
import { useDiscoveryStore } from '@/stores/discoveryStore';
import Button from '@/components/ui/Button.vue';
import ButtonGroup from '@/components/ui/ButtonGroup.vue';
import RangeSlider from '@/components/ui/RangeSlider.vue';
import SystemListItem from '@/components/settings/categories/multiroom/SystemListItem.vue';
import MessageContent from '@/components/ui/MessageContent.vue';
import SvgIcon from '@/components/ui/SvgIcon.vue';
import SettingsContainer from '@/components/settings/SettingsContainer.vue';
import SettingsSection from '@/components/settings/SettingsSection.vue';
import SectionHeader from '@/components/settings/SectionHeader.vue';
import SettingItem from '@/components/settings/SettingItem.vue';

const emit = defineEmits(['edit-zone', 'create-zone', 'edit-client', 'configure-system']);

const { t } = useI18n();
const snapcastStore = useSnapcastStore();
const unifiedStore = useUnifiedAudioStore();
const multiroomClientStore = useMultiroomStore();
const discoveryStore = useDiscoveryStore();

// Multiroom state
const isMultiroomActive = computed(() => unifiedStore.systemState.multiroom_enabled);

// Message display logic (reads centralized transitionState from multiroomStore)
const showMessage = computed(() => {
  return multiroomClientStore.transitionState !== 'idle' || !isMultiroomActive.value;
});
const isLoading = computed(() => multiroomClientStore.transitionState === 'enabling');
const messageTitle = computed(() => {
  if (multiroomClientStore.transitionState === 'error') {
    return multiroomClientStore.transitionError || t('multiroom.error');
  }
  return multiroomClientStore.transitionState === 'enabling' ? t('multiroom.starting') : t('multiroom.disabled');
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

// Unified discovery list: pending ethernet clients + visible wifi hotspots.
// Each item carries a discovery `source` so the parent knows which adoption
// flow to launch (ethernet via configure-pending, wifi via adopt-speaker).
const discoveryItems = computed(() => {
  const items = [];

  for (const client of multiroomClientStore.pendingClientList) {
    const configuring = multiroomClientStore.isClientConfiguring(client.mac_id);
    items.push({
      key: `eth:${client.mac_id}`,
      source: 'ethernet',
      name: client.name || client.ip,
      status: configuring ? t('multiroom.pending.rebooting') : t('multiroom.pending.notConfigured'),
      statusVariant: configuring ? 'configuring' : '',
      disabled: configuring,
      macId: client.mac_id
    });
  }

  for (const hotspot of discoveryStore.hotspots) {
    items.push({
      key: `wifi:${hotspot.ssid}`,
      source: 'wifi',
      name: hotspot.ssid,
      status: t('multiroom.pending.notConfigured'),
      statusVariant: '',
      disabled: false,
      ssid: hotspot.ssid,
      signal: hotspot.signal
    });
  }

  return items;
});

// Navigation: dispatch to ConfigureSystem with the right discovery context.
function handleDiscoveryClick(item) {
  if (item.disabled) return;
  if (item.source === 'ethernet') {
    emit('configure-system', { source: 'ethernet', macId: item.macId });
  } else {
    emit('configure-system', {
      source: 'wifi',
      ssid: item.ssid,
      signal: item.signal
    });
  }
}

function handleEditZone(groupId) {
  emit('edit-zone', groupId);
}

function handleCreateZone() {
  emit('create-zone');
}

function handleEditClient(macId) {
  emit('edit-client', macId);
}

// Presets come from the backend capabilities (single source of truth);
// only the display name is resolved here, via i18n keyed on the preset id
// (snake_case id → camelCase key: lan, wifiStable, wifiWeak).
const audioPresets = computed(() =>
  snapcastStore.capabilities.presets.map(preset => ({
    id: preset.id,
    name: t(`multiroomSettings.${preset.id.replace(/_([a-z])/g, (_, c) => c.toUpperCase())}`),
    config: preset.config
  }))
);

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
    current.buffer_ms === preset.config.buffer_ms &&
    current.codec === preset.config.codec &&
    current.chunk_ms === preset.config.chunk_ms &&
    current.snapclient_buffer_time === preset.config.snapclient_buffer_time
  );
  return active?.id || null;
});

// Codec options for ButtonGroup — the list comes from the backend
// capabilities; only the display casing is presentation-side.
const CODEC_LABELS = { flac: 'FLAC', pcm: 'PCM', opus: 'Opus', ogg: 'Ogg' };
const codecOptions = computed(() =>
  snapcastStore.capabilities.codecs.map(codec => ({
    label: CODEC_LABELS[codec] || codec.toUpperCase(),
    value: codec
  }))
);

// === MULTIROOM - CLIENTS ===

async function loadMultiroomData() {
  // Load clients, server config, and pending clients
  // Zone/client data comes from multiroomStore (initialized in App.vue)
  await Promise.all([
    snapcastStore.loadClients(),
    snapcastStore.loadServerConfig(),
    multiroomClientStore.fetchPendingClients(),
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

// Reload data when multiroom becomes ready after a transition
watch(() => multiroomClientStore.transitionState, (newState, oldState) => {
  if (newState === 'idle' && (oldState === 'enabling' || oldState === 'disabling')) {
    loadMultiroomData();
  }
});

onMounted(async () => {
  if (isMultiroomActive.value) {
    // loadMultiroomData() already fetches pending clients
    await loadMultiroomData();
  } else {
    // Fetch pending clients even when multiroom is off (they register regardless)
    multiroomClientStore.fetchPendingClients();
  }

  // Start hotspot polling + load the server's wifi creds for adoption auto-fill.
  discoveryStore.startPolling();
  discoveryStore.loadServerWifiCreds();
});

onBeforeUnmount(() => {
  discoveryStore.stopPolling();
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
  background: var(--color-warning-subtle);
  color: var(--color-warning);
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

/* Discovered speakers list (pending ethernet + wifi hotspots) */
.discovery-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
}

/* Responsive */
@media (max-aspect-ratio: 4/3) {
  .zone-clients,
  .ungrouped-clients {
    grid-template-columns: 1fr;
  }
}
</style>
