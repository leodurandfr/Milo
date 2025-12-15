<!-- frontend/src/components/dsp/DspModal.vue -->
<!-- Main DSP control panel with parametric EQ and presets -->
<template>
  <div class="dsp-modal">
    <!-- Header: "Égaliseur DSP" + Toggle ON/OFF -->
    <ModalHeader :title="$t('dsp.title', 'Égaliseur DSP')">
      <template #actions>
        <!-- DSP Enable/Disable toggle -->
        <Toggle
          :model-value="dspStore.isDspEnabled"
          :disabled="dspStore.isTogglingEnabled"
          @change="handleDspToggle"
        />
      </template>
    </ModalHeader>

    <!-- Main content -->
    <div class="content-wrapper">
      <Transition name="fade-slide" mode="out-in">
        <!-- MESSAGE: DSP disabled -->
        <MessageContent
          v-if="!dspStore.isDspEnabled"
          key="message"
          icon="equalizer"
          :title="$t('dsp.disabled', 'DSP is disabled')"
        />

        <!-- DSP CONTROLS -->
        <div v-else key="controls" class="dsp-controls">
          <!-- Connection status indicator -->
          <div v-if="!dspStore.isConnected" class="connection-status">
            <LoadingSpinner v-if="dspStore.isLoading" size="small" />
            <span class="status-text">{{ $t('dsp.connecting', 'Connecting to DSP...') }}</span>
          </div>

          <template v-if="dspStore.isConnected">
            <!-- Section 1: Zones (tabs + volumes) -->
            <ZoneTabs
              ref="zoneTabsRef"
              :disabled="dspStore.isUpdating"
              @open-link-dialog="showLinkedClientsDialog = true"
            />

            <!-- Section 2: 10 Bands Equalizer with presets dropdown -->
            <section class="settings-section">
              <div class="section-group">
                <div class="section-header">
                  <h2 class="heading-2">
                    {{ $t('dsp.equalizer.title', '10 Bands Equalizer') }}
                    <span v-if="selectedZoneName" class="zone-suffix">· {{ selectedZoneName }}</span>
                  </h2>
                  <Dropdown
                    v-model="currentPreset"
                    :options="presetOptions"
                    :placeholder="$t('dsp.selectPreset', 'Preset')"
                    :disabled="dspStore.isUpdating"
                    @change="handlePresetChange"
                  />
                </div>
                <ParametricEQ
                  :filters="dspStore.filters"
                  :filters-loaded="dspStore.filtersLoaded"
                  :disabled="dspStore.isUpdating"
                  :is-mobile="isMobile"
                  @update:filter="handleFilterUpdate"
                  @change="handleFilterChange"
                />
              </div>
            </section>

            <!-- Section 3: Advanced DSP (Compressor, Loudness, Delay) -->
            <AdvancedDsp :zone-name="selectedZoneName" />

            <!-- Level Meters -->
            <LevelMeters />
          </template>
        </div>
      </Transition>
    </div>

    <!-- Linked Clients Dialog -->
    <LinkedClientsDialog
      :is-open="showLinkedClientsDialog"
      @close="showLinkedClientsDialog = false"
    />
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue';
import { useDspStore } from '@/stores/dspStore';
import { useI18n } from '@/services/i18n';
import useWebSocket from '@/services/websocket';
import ModalHeader from '@/components/ui/ModalHeader.vue';
import Toggle from '@/components/ui/Toggle.vue';
import Dropdown from '@/components/ui/Dropdown.vue';
import MessageContent from '@/components/ui/MessageContent.vue';
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue';
import ZoneTabs from './ZoneTabs.vue';
import ParametricEQ from './ParametricEQ.vue';
import AdvancedDsp from './AdvancedDsp.vue';
import LevelMeters from './LevelMeters.vue';
import LinkedClientsDialog from './LinkedClientsDialog.vue';

const { t } = useI18n();
const dspStore = useDspStore();
const { on } = useWebSocket();

// Local state
const isMobile = ref(false);
const showLinkedClientsDialog = ref(false);
const currentPreset = ref('');
const zoneTabsRef = ref(null);

// Selected zone/client name from ZoneTabs component
const selectedZoneName = computed(() => {
  return zoneTabsRef.value?.selectedZoneName ?? '';
});

let unsubscribeFunctions = [];

// === PRESETS ===
// Default presets with gain values for 10 EQ bands
const defaultPresets = computed(() => [
  {
    id: 'flat',
    label: t('dsp.quickPresets.flat', 'Flat'),
    gains: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
  },
  {
    id: 'bass_boost',
    label: t('dsp.quickPresets.bassBoost', 'Bass Boost'),
    gains: [6, 5, 3, 1, 0, 0, 0, 0, 0, 0]
  },
  {
    id: 'vocal',
    label: t('dsp.quickPresets.vocal', 'Vocal'),
    gains: [-2, -1, 0, 1, 2, 3, 2, 1, 0, 0]
  },
  {
    id: 'night',
    label: t('dsp.quickPresets.nightMode', 'Night'),
    gains: [-4, -2, 0, 1, 1, 1, 0, -1, -2, -3]
  }
]);

const userPresets = computed(() => dspStore.presets);

// Convert presets to Dropdown options format
const presetOptions = computed(() => {
  const options = [];

  // Default presets group
  defaultPresets.value.forEach(preset => {
    options.push({
      label: preset.label,
      value: `default:${preset.id}`
    });
  });

  // User presets group (if any)
  if (userPresets.value.length > 0) {
    userPresets.value.forEach(preset => {
      options.push({
        label: preset,
        value: `user:${preset}`
      });
    });
  }

  return options;
});

// === MOBILE DETECTION ===
function updateMobileStatus() {
  const aspectRatio = window.innerWidth / window.innerHeight;
  isMobile.value = aspectRatio <= 4 / 3;
}

// === DSP TOGGLE ===
async function handleDspToggle(enabled) {
  await dspStore.toggleDspEnabled(enabled);
}

// === FILTER UPDATES ===
function handleFilterUpdate({ id, field, value }) {
  dspStore.updateFilter(id, field, value);
}

function handleFilterChange({ id, field, value }) {
  dspStore.finalizeFilterUpdate(id);
}

// === PRESET HANDLING ===
async function handlePresetChange(value) {
  if (!value) return;

  if (value.startsWith('default:')) {
    // Apply default preset
    const presetId = value.slice(8);
    const preset = defaultPresets.value.find(p => p.id === presetId);
    if (preset) {
      await applyDefaultPreset(preset);
    }
  } else if (value.startsWith('user:')) {
    // Load user preset
    const presetName = value.slice(5);
    await dspStore.loadPreset(presetName);
  }
}

async function applyDefaultPreset(preset) {
  for (let i = 0; i < dspStore.filters.length && i < preset.gains.length; i++) {
    const filter = dspStore.filters[i];
    if (filter.gain !== preset.gains[i]) {
      dspStore.updateFilter(filter.id, 'gain', preset.gains[i]);
      await dspStore.finalizeFilterUpdate(filter.id);
    }
  }
}

// Watch for active preset changes from store
watch(() => dspStore.activePreset, (newPreset) => {
  if (newPreset && !currentPreset.value.startsWith('default:')) {
    currentPreset.value = 'user:' + newPreset;
  }
}, { immediate: true });

// === WEBSOCKET HANDLERS ===
function handleDspFilterChanged(event) {
  dspStore.handleFilterChanged(event);
}

function handleDspFiltersReset() {
  dspStore.handleFiltersReset();
}

function handleDspStateChanged(event) {
  dspStore.handleStateChanged(event);
}

function handleDspPresetLoaded(event) {
  dspStore.handlePresetLoaded(event);
}

// === LIFECYCLE ===
onMounted(async () => {
  updateMobileStatus();
  window.addEventListener('resize', updateMobileStatus);

  // Initialize filters
  dspStore.initializeFilters();

  // Load enabled state from settings
  await dspStore.loadEnabledState();

  // Load available DSP targets (Milo + clients)
  await dspStore.loadTargets();

  // Load DSP status if enabled
  if (dspStore.isDspEnabled) {
    await dspStore.loadStatus();
  }

  // Subscribe to WebSocket events
  unsubscribeFunctions.push(
    on('dsp', 'filter_changed', handleDspFilterChanged),
    on('dsp', 'filters_reset', handleDspFiltersReset),
    on('dsp', 'state_changed', handleDspStateChanged),
    on('dsp', 'preset_loaded', handleDspPresetLoaded),
    on('dsp', 'compressor_changed', (e) => dspStore.handleCompressorChanged(e)),
    on('dsp', 'loudness_changed', (e) => dspStore.handleLoudnessChanged(e)),
    on('dsp', 'delay_changed', (e) => dspStore.handleDelayChanged(e)),
    on('dsp', 'volume_changed', (e) => dspStore.handleVolumeChanged(e)),
    on('dsp', 'mute_changed', (e) => dspStore.handleMuteChanged(e)),
    on('dsp', 'links_changed', (e) => dspStore.handleLinksChanged(e)),
    on('dsp', 'enabled_changed', (e) => dspStore.handleEnabledChanged(e))
  );
});

onUnmounted(() => {
  window.removeEventListener('resize', updateMobileStatus);
  unsubscribeFunctions.forEach(unsubscribe => unsubscribe());
  dspStore.cleanup();
});
</script>

<style scoped>
.dsp-modal {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

.content-wrapper {
  display: flex;
  flex-direction: column;
  position: relative;
}

.dsp-controls {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

.connection-status {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-02);
  padding: var(--space-05);
  background: var(--color-background-neutral);
  border-radius: var(--radius-06);
  color: var(--color-text-secondary);
}

.status-text {
  font-size: 14px;
}

/* Settings section pattern */
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

.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-03);
}

.section-header :deep(.dropdown) {
  max-width: 200px;
}

.zone-suffix {
  color: var(--color-text-secondary);
  font-weight: normal;
}

/* Transitions */
.fade-slide-enter-active,
.fade-slide-leave-active {
  transition: opacity var(--transition-normal), transform var(--transition-normal);
}

.fade-slide-enter-from {
  opacity: 0;
  transform: translateY(8px);
}

.fade-slide-leave-to {
  opacity: 0;
  transform: translateY(-8px);
}

/* Mobile adjustments */
@media (max-aspect-ratio: 4/3) {
  .dsp-modal {
    gap: var(--space-02);
  }

  .settings-section {
    border-radius: var(--radius-05);
  }
}
</style>
