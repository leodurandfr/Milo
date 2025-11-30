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
            <!-- Multiroom speakers -->
            <section class="settings-section">
              <div class="multiroom-group">
                <h2 class="heading-2">{{ t('multiroom.speakers') }}</h2>

                <div v-if="multiroomStore.isLoading" class="loading-state">
                  <p class="text-mono">{{ t('multiroom.loadingSpeakers') }}</p>
                </div>

                <div v-else-if="sortedMultiroomClients.length === 0" class="no-clients-state">
                  <p class="text-mono">{{ t('multiroom.noSpeakers') }}</p>
                </div>

                <div v-else class="clients-list" :style="clientsGridStyle">
                  <div v-for="(client, index) in sortedMultiroomClients" :key="client.id" class="client-config-item"
                    :style="getClientGridStyle(index)">
                    <div class="client-info-wrapper">
                      <span class="client-hostname text-mono">{{ client.host }}</span>
                      <InputText v-model="clientNames[client.id]" :placeholder="client.host"
                        size="small" :maxlength="50" @blur="handleInputBlur(client.id)" />
                    </div>
                  </div>
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
import Button from '@/components/ui/Button.vue';
import RangeSlider from '@/components/ui/RangeSlider.vue';
import InputText from '@/components/ui/InputText.vue';
import MessageContent from '@/components/ui/MessageContent.vue';

const { t } = useI18n();
const { on } = useWebSocket();
const multiroomStore = useMultiroomStore();
const unifiedStore = useUnifiedAudioStore();

// Multiroom state
const isMultiroomActive = computed(() => unifiedStore.systemState.multiroom_enabled);

// ResizeObserver to animate height
const contentWrapperRef = ref(null);
const containerHeight = ref('auto');
let resizeObserver = null;
let isFirstResize = true;

// Client names (local state for input)
const clientNames = ref({});

// Sorted clients with "milo" first
const sortedMultiroomClients = computed(() => multiroomStore.sortedClients);

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

// Dynamic style for the grid
const clientsGridStyle = computed(() => {
  const count = sortedMultiroomClients.value.length;

  if (count <= 1) {
    return { display: 'flex', flexDirection: 'column' };
  }

  if (count <= 3) {
    return {
      display: 'flex',
      gap: 'var(--space-02)'
    };
  }

  if (count === 4) {
    return {
      display: 'grid',
      gridTemplateColumns: 'repeat(2, 1fr)',
      gap: 'var(--space-02)'
    };
  }

  // 5+ items: 3-column grid
  return {
    display: 'grid',
    gridTemplateColumns: 'repeat(3, 1fr)',
    gap: 'var(--space-02)'
  };
});

// Grid position for each item (short row on top)
const getClientGridStyle = (index) => {
  const count = sortedMultiroomClients.value.length;

  // No specific positioning for 1–4 items
  if (count <= 4) return {};

  const remainder = count % 3;

  // If divisible by 3, no reordering needed
  if (remainder === 0) return {};

  // First row: remainder items
  if (index < remainder) {
    return {
      gridRow: 1,
      gridColumn: index + 1
    };
  }

  // Following rows: groups of 3
  const adjustedIndex = index - remainder;
  const row = Math.floor(adjustedIndex / 3) + 2;
  const col = (adjustedIndex % 3) + 1;

  return {
    gridRow: row,
    gridColumn: col
  };
};

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
    multiroomStore.loadServerConfig()
  ]);

  // Initialize local names
  clientNames.value = {};
  multiroomStore.clients.forEach(client => {
    clientNames.value[client.id] = client.name || client.host;
  });
}

async function updateClientName(clientId) {
  const newName = clientNames.value[clientId]?.trim();
  if (!newName) return;

  await multiroomStore.updateClientName(clientId, newName);
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

// === INPUT HANDLERS ===

function handleInputBlur(clientId) {
  // Save changes when the user leaves the field
  updateClientName(clientId);
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

// WebSocket listener
const handleClientNameChanged = (msg) => {
  const { client_id, name } = msg.data;
  if (clientNames.value[client_id] !== undefined) {
    clientNames.value[client_id] = name;
  }
  // The store updates itself via its own handler
  multiroomStore.handleClientNameChanged(msg);
};

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

  on('snapcast', 'client_name_changed', handleClientNameChanged);
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

.clients-list {
  /* Style is applied dynamically via clientsGridStyle */
}

.client-config-item {
  background: var(--color-background-strong);
  border-radius: var(--radius-04);
  padding: var(--space-03);
  width: 100%;
}

.client-info-wrapper {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.client-hostname {
  color: var(--color-text-secondary);
  font-size: var(--font-size-small);
}

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

  .clients-list {
    display: flex !important;
    flex-direction: column !important;
    gap: var(--space-02) !important;
  }

  .client-config-item {
    grid-row: unset !important;
    grid-column: unset !important;
  }
}
</style>