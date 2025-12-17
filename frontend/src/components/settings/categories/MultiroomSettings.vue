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
            <!-- Zones & Speakers Section -->
            <section class="settings-section">
              <div class="multiroom-group">
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
                    <ListItemButton
                      :title="`Zone · ${zone.displayName}`"
                      action="caret"
                      @click="handleEditZone(zone.id)"
                    />
                    <!-- Zone clients -->
                    <div class="zone-clients">
                      <ListItemButton
                        v-for="client in zone.clients"
                        :key="client.id"
                        :title="`${client.name} · ${getSpeakerTypeLabel(client.dsp_id)}`"
                        action="caret"
                        @click="handleEditClient(client.id)"
                      />
                    </div>
                  </div>

                  <!-- Individual speakers section -->
                  <template v-if="ungroupedClients.length > 0">
                    <h3 v-if="zones.length > 0" class="heading-3 section-subtitle">{{ t('multiroom.individualSpeakers') }}</h3>
                    <ListItemButton
                      v-for="client in ungroupedClients"
                      :key="client.id"
                      :title="`${client.name} · ${getSpeakerTypeLabel(client.dsp_id)}`"
                      action="caret"
                      @click="handleEditClient(client.id)"
                    />
                  </template>
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
import Button from '@/components/ui/Button.vue';
import RangeSlider from '@/components/ui/RangeSlider.vue';
import ListItemButton from '@/components/ui/ListItemButton.vue';
import MessageContent from '@/components/ui/MessageContent.vue';

const emit = defineEmits(['edit-zone', 'create-zone', 'edit-client']);

const { t } = useI18n();
const { on } = useWebSocket();
const multiroomStore = useMultiroomStore();
const unifiedStore = useUnifiedAudioStore();
const dspStore = useDspStore();

// Multiroom state
const isMultiroomActive = computed(() => unifiedStore.systemState.multiroom_enabled);

// ResizeObserver to animate height
const contentWrapperRef = ref(null);
const containerHeight = ref('auto');
let resizeObserver = null;
let isFirstResize = true;

// Sorted clients with "milo" first
const sortedMultiroomClients = computed(() => multiroomStore.sortedClients);

// Get zones with client details
const zones = computed(() => {
  return dspStore.linkedGroups.map((group, index) => {
    const clients = (group.client_ids || [])
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
  gap: var(--space-01);
}

/* Zone clients (indented under zone) */
.zone-clients {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
  padding-left: var(--space-04);
}

/* Section subtitle (e.g., "Individual speakers") */
.section-subtitle {
  color: var(--color-text-secondary);
  margin-top: var(--space-03);
  margin-bottom: var(--space-01);
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
}
</style>
