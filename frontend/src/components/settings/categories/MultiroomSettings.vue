<!-- frontend/src/components/settings/categories/MultiroomSettings.vue -->
<template>
  <div class="multiroom-settings">
    <!-- Main content with animated height -->
    <div class="content-container" :style="{ height: containerHeight }">
      <div class="content-wrapper" ref="contentWrapperRef" :class="{ 'with-background': !isMultiroomActive }">
        <!-- MESSAGE: Multiroom disabled -->
        <Transition name="fade-slide">
          <MessageContent v-if="!isMultiroomActive" key="message" icon="multiroom" :title="t('multiroom.disabled')" />
        </Transition>

        <!-- SETTINGS: Sections visible only if multiroom is enabled -->
        <Transition name="settings">
          <div v-if="isMultiroomActive" key="settings" class="settings-container">
            <!-- Speakers & Zones Section -->
            <section class="settings-section">
              <div class="multiroom-group">
                <h2 class="heading-2">{{ t('multiroom.speakers') }}</h2>

                <div v-if="multiroomStore.isLoading" class="loading-state">
                  <p class="text-mono">{{ t('multiroom.loadingSpeakers') }}</p>
                </div>

                <div v-else-if="sortedMultiroomClients.length === 0" class="no-clients-state">
                  <p class="text-mono">{{ t('multiroom.noSpeakers') }}</p>
                </div>

                <div v-else class="speakers-list">
                  <!-- Zones -->
                  <div
                    v-for="zone in zones"
                    :key="zone.id"
                    class="zone-item"
                  >
                    <!-- Zone Header (Expandable) -->
                    <div
                      class="zone-header"
                      @click="toggleZoneExpansion(zone.id)"
                    >
                      <span class="zone-expand-icon">{{ expandedZones.has(zone.id) ? '▼' : '▶' }}</span>
                      <span class="zone-name heading-3">{{ zone.displayName }}</span>
                      <span class="zone-count text-mono-small">{{ zone.clientCount }} {{ t('multiroom.speakers').toLowerCase() }}</span>
                      <IconButton
                        icon="caretRight"
                        variant="background-strong"
                        size="small"
                        @click.stop="handleEditZone(zone.id)"
                      />
                    </div>

                    <!-- Zone Members (Expanded) with volume/mute controls -->
                    <Transition name="expand">
                      <div v-if="expandedZones.has(zone.id)" class="zone-members">
                        <div
                          v-for="client in zone.clients"
                          :key="client.id"
                          class="client-volume-row"
                          :class="{ 'client-muted': client.muted }"
                        >
                          <span class="client-name heading-3" :class="{ 'muted': client.muted }">
                            {{ client.name }}
                          </span>
                          <div class="volume-control">
                            <RangeSlider
                              :model-value="getClientVolume(client)"
                              :min="volumeLimits.min_db"
                              :max="volumeLimits.max_db"
                              :step="1"
                              show-value
                              value-unit=" dB"
                              :disabled="client.muted"
                              @input="(v) => handleVolumeInput(client, v)"
                              @change="(v) => handleVolumeChange(client, v)"
                            />
                          </div>
                          <Toggle
                            :model-value="!client.muted"
                            type="background-strong"
                            @change="(enabled) => handleMuteToggle(client, !enabled)"
                          />
                        </div>
                      </div>
                    </Transition>
                  </div>

                  <!-- Separator (if both zones and ungrouped clients exist) -->
                  <div v-if="zones.length > 0 && ungroupedClients.length > 0" class="separator"></div>

                  <!-- Ungrouped Clients -->
                  <div
                    v-for="client in ungroupedClients"
                    :key="client.id"
                    class="ungrouped-client"
                  >
                    <div class="ungrouped-client-info">
                      <span class="client-hostname text-mono-small">{{ client.host }}</span>
                      <span class="client-arrow">→</span>
                      <span class="client-name heading-4">{{ client.name }}</span>
                    </div>
                    <IconButton
                      icon="caretRight"
                      variant="background-strong"
                      size="small"
                      @click="handleEditClient(client.id)"
                    />
                  </div>

                  <!-- Create Zone Button -->
                  <Button
                    v-if="sortedMultiroomClients.length >= 2"
                    variant="brand"
                    size="medium"
                    class="create-zone-button"
                    @click="handleCreateZone"
                  >
                    {{ t('dsp.zones.createZone', 'Create Zone') }}
                  </Button>
                </div>
              </div>
            </section>

            <!-- Audio presets -->
            <section class="settings-section">
              <div class="multiroom-group">
                <h2 class="heading-2">{{ t('multiroomSettings.presets') }}</h2>
                <div class="presets-buttons">
                  <Button v-for="preset in audioPresets" :key="preset.id"
                    :variant="isPresetActive(preset) ? 'outline' : 'background-strong'" size="medium"
                    :disabled="multiroomStore.isApplyingServerConfig"
                    @click="applyPreset(preset)">
                    {{ preset.name }}
                  </Button>
                </div>
              </div>
            </section>

            <!-- Advanced settings -->
            <section class="settings-section">
              <div class="multiroom-group">
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
                  <div class="codec-buttons">
                    <Button :variant="multiroomStore.serverConfig.codec === 'opus' ? 'outline' : 'background-strong'" size="medium"
                      @click="selectCodec('opus')">
                      Opus
                    </Button>
                    <Button :variant="multiroomStore.serverConfig.codec === 'flac' ? 'outline' : 'background-strong'" size="medium"
                      @click="selectCodec('flac')">
                      FLAC
                    </Button>
                    <Button :variant="multiroomStore.serverConfig.codec === 'pcm' ? 'outline' : 'background-strong'" size="medium"
                      @click="selectCodec('pcm')">
                      PCM
                    </Button>
                  </div>
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

  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue';
import { useI18n } from '@/services/i18n';
import useWebSocket from '@/services/websocket';
import { useMultiroomStore } from '@/stores/multiroomStore';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useDspStore } from '@/stores/dspStore';
import { useSettingsStore } from '@/stores/settingsStore';
import Button from '@/components/ui/Button.vue';
import RangeSlider from '@/components/ui/RangeSlider.vue';
import IconButton from '@/components/ui/IconButton.vue';
import Toggle from '@/components/ui/Toggle.vue';
import MessageContent from '@/components/ui/MessageContent.vue';

const emit = defineEmits(['edit-zone', 'create-zone', 'edit-client']);

const { t } = useI18n();
const { on } = useWebSocket();
const multiroomStore = useMultiroomStore();
const unifiedStore = useUnifiedAudioStore();
const dspStore = useDspStore();
const settingsStore = useSettingsStore();

// Multiroom state
const isMultiroomActive = computed(() => unifiedStore.systemState.multiroom_enabled);

// ResizeObserver to animate height
const contentWrapperRef = ref(null);
const containerHeight = ref('auto');
let resizeObserver = null;
let isFirstResize = true;

// Zone expansion state
const expandedZones = ref(new Set());

// Volume limits from settings
const volumeLimits = computed(() => settingsStore.volumeLimits);

// Local volume cache for responsive UI during drag
const localVolumeCache = ref({});

// Throttling for volume updates
const throttleTimeouts = new Map();
const finalTimeouts = new Map();

// Sorted clients with "milo" first
const sortedMultiroomClients = computed(() => multiroomStore.sortedClients);

// Get zones with client details (including volume and muted state)
const zones = computed(() => {
  return dspStore.linkedGroups.map((group, index) => {
    const clients = (group.client_ids || [])
      .map(dspId => {
        const client = multiroomStore.clients.find(c => c.dsp_id === dspId);
        return client ? {
          id: client.id,
          dsp_id: dspId,
          host: client.host,
          name: client.name || client.host,
          muted: client.muted || false,
          volume: dspStore.getClientDspVolume(dspId)
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

// Toggle zone expansion
function toggleZoneExpansion(zoneId) {
  if (expandedZones.value.has(zoneId)) {
    expandedZones.value.delete(zoneId);
  } else {
    expandedZones.value.add(zoneId);
  }
  // Force reactivity
  expandedZones.value = new Set(expandedZones.value);
}

// === ZONE CLIENT VOLUME/MUTE HANDLERS ===

// Get client volume (from local cache or dspStore)
function getClientVolume(client) {
  if (localVolumeCache.value[client.dsp_id] !== undefined) {
    return localVolumeCache.value[client.dsp_id];
  }
  const volume = dspStore.getClientDspVolume(client.dsp_id);
  return Math.max(volumeLimits.value.min_db, Math.min(volumeLimits.value.max_db, Math.round(volume)));
}

// Handle volume input (during slider drag)
function handleVolumeInput(client, value) {
  // Update local cache for responsive UI
  localVolumeCache.value[client.dsp_id] = value;

  // Clear existing timeouts
  if (throttleTimeouts.has(client.id)) {
    clearTimeout(throttleTimeouts.get(client.id));
  }
  if (finalTimeouts.has(client.id)) {
    clearTimeout(finalTimeouts.get(client.id));
  }

  // Throttled update (50ms)
  throttleTimeouts.set(client.id, setTimeout(async () => {
    await dspStore.updateClientDspVolume(client.dsp_id, value);
  }, 50));

  // Final update (500ms)
  finalTimeouts.set(client.id, setTimeout(async () => {
    await dspStore.updateClientDspVolume(client.dsp_id, value);
  }, 500));
}

// Handle volume change (on slider release)
function handleVolumeChange(client, value) {
  // Clear throttle timeouts
  if (throttleTimeouts.has(client.id)) {
    clearTimeout(throttleTimeouts.get(client.id));
    throttleTimeouts.delete(client.id);
  }
  if (finalTimeouts.has(client.id)) {
    clearTimeout(finalTimeouts.get(client.id));
    finalTimeouts.delete(client.id);
  }

  // Clear local cache
  delete localVolumeCache.value[client.dsp_id];

  // Final update
  dspStore.updateClientDspVolume(client.dsp_id, value);
  dspStore.setClientDspVolume(client.dsp_id, value);
}

// Handle mute toggle
async function handleMuteToggle(client, muted) {
  await multiroomStore.toggleClientMute(client.id, muted);
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

function isPresetActive(preset) {
  const current = multiroomStore.serverConfig;
  const presetConfig = preset.config;
  return current.buffer === presetConfig.buffer &&
    current.codec === presetConfig.codec &&
    current.chunk_ms === presetConfig.chunk_ms;
}

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

function applyPreset(preset) {
  multiroomStore.applyPreset(preset);
}

function selectCodec(codecName) {
  multiroomStore.selectCodec(codecName);
}

async function applyServerConfig() {
  await multiroomStore.applyServerConfig();
}

// === RESIZE OBSERVER ===
function setupResizeObserver() {
  if (resizeObserver) {
    resizeObserver.disconnect();
  }

  resizeObserver = new ResizeObserver(entries => {
    if (entries[0]) {
      const newHeight = entries[0].contentRect.height;

      // First time: initialize without transition
      if (isFirstResize) {
        containerHeight.value = `${newHeight}px`;
        isFirstResize = false;
        return;
      }

      const currentHeight = parseFloat(containerHeight.value);

      if (Math.abs(newHeight - currentHeight) > 2) {
        containerHeight.value = `${newHeight}px`;
      }
    }
  });

  if (contentWrapperRef.value) {
    resizeObserver.observe(contentWrapperRef.value);
  }
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

  // Setup ResizeObserver after next tick so the ref is available
  await nextTick();
  setupResizeObserver();

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
});

onUnmounted(() => {
  if (resizeObserver) {
    resizeObserver.disconnect();
  }
  // Clear all throttle timeouts
  throttleTimeouts.forEach(timeout => clearTimeout(timeout));
  finalTimeouts.forEach(timeout => clearTimeout(timeout));
});
</script>

<style scoped>
.multiroom-settings {
  display: flex;
  flex-direction: column;
}

.content-container {
  transition: height var(--transition-spring);
  overflow: visible;
  position: relative;
}

.content-wrapper {
  display: flex;
  flex-direction: column;
  overflow: visible;
  border-radius: var(--radius-07);
  transition: background 400ms ease;
  position: relative;
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
  gap: var(--space-04);
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

/* Zone item */
.zone-item {
  background: var(--color-background-strong);
  border-radius: var(--radius-04);
  overflow: hidden;
}

.zone-header {
  display: flex;
  align-items: center;
  gap: var(--space-03);
  padding: var(--space-03);
  cursor: pointer;
  transition: background var(--transition-fast);
}

.zone-header:hover {
  background: var(--color-background-medium);
}

.zone-expand-icon {
  font-size: 10px;
  color: var(--color-text-secondary);
  width: 12px;
  text-align: center;
}

.zone-name {
  flex: 1;
  color: var(--color-text);
}

.zone-count {
  color: var(--color-text-secondary);
}

/* Zone members (expanded) */
.zone-members {
  display: flex;
  flex-direction: column;
  border-top: 1px solid var(--color-border);
  padding: 0 var(--space-03);
}

/* Client volume row (pattern from ZoneTabs) */
.client-volume-row {
  display: grid;
  grid-template-columns: minmax(100px, 150px) 1fr auto;
  align-items: center;
  gap: var(--space-03);
  padding: var(--space-03) 0;
  position: relative;
}

.client-volume-row:not(:last-child) {
  border-bottom: 1px solid var(--color-border);
}

.client-volume-row .client-name {
  color: var(--color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  transition: color var(--transition-fast);
}

.client-volume-row .client-name.muted {
  color: var(--color-text-light);
}

.client-volume-row .volume-control {
  flex: 1;
  min-width: 0;
}

/* Separator */
.separator {
  height: 1px;
  background: var(--color-border);
  margin: var(--space-02) 0;
}

/* Ungrouped client */
.ungrouped-client {
  display: flex;
  align-items: center;
  gap: var(--space-03);
  padding: var(--space-03);
  background: var(--color-background-strong);
  border-radius: var(--radius-04);
}

.ungrouped-client-info {
  flex: 1;
  display: flex;
  align-items: center;
  gap: var(--space-02);
  min-width: 0;
}

.client-hostname {
  color: var(--color-text-secondary);
  white-space: nowrap;
}

.client-arrow {
  color: var(--color-text-light);
}

.client-name {
  color: var(--color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* Create zone button */
.create-zone-button {
  margin-top: var(--space-02);
}

/* Presets and codec buttons */
.presets-buttons {
  display: flex;
  gap: var(--space-02);
}

.presets-buttons .btn {
  flex: 1;
}

.form-group {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.form-group label {
  color: var(--color-text-secondary);
}

.codec-buttons {
  display: flex;
  gap: var(--space-02);
}

.codec-buttons .btn {
  flex: 1;
}

.apply-button-sticky {
  position: sticky;
  bottom: 0;
  width: 100%;
  z-index: 10;
}

/* Expand transition */
.expand-enter-active,
.expand-leave-active {
  transition: all var(--transition-normal);
  overflow: hidden;
}

.expand-enter-from,
.expand-leave-to {
  opacity: 0;
  max-height: 0;
}

.expand-enter-to,
.expand-leave-from {
  opacity: 1;
  max-height: 500px;
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

  .codec-buttons,
  .presets-buttons {
    flex-direction: column;
  }

  /* Mobile: stack volume control below name */
  .client-volume-row {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-02);
    padding: var(--space-02) 0;
  }

  .client-volume-row .client-name {
    flex: 1;
    min-width: 0;
  }

  .client-volume-row .volume-control {
    order: 3;
    width: 100%;
    flex-basis: 100%;
  }
}
</style>
