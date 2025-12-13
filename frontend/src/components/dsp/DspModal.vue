<!-- frontend/src/components/dsp/DspModal.vue -->
<!-- Main DSP control panel with parametric EQ and presets -->
<template>
  <div class="dsp-modal">
    <!-- Header with toggle and actions -->
    <ModalHeader :title="$t('dsp.title', 'DSP')">
      <template #actions="{ iconVariant }">
        <!-- Target selector (Milo or clients) -->
        <Dropdown
          v-if="isDspEnabled && targetOptions.length > 1"
          v-model="selectedTargetId"
          :options="targetOptions"
          :placeholder="$t('dsp.selectTarget', 'Device')"
          variant="minimal"
          :disabled="dspStore.isLoading"
          @change="handleTargetChange"
        />

        <!-- Link clients button -->
        <IconButton
          v-if="isDspEnabled && targetOptions.length > 1"
          icon="link"
          :variant="isTargetLinked ? 'brand' : iconVariant"
          :disabled="dspStore.isLoading"
          @click="showLinkedClientsDialog = true"
        />

        <!-- Preset selector -->
        <Dropdown
          v-if="isDspEnabled"
          v-model="selectedPreset"
          :options="presetOptions"
          :placeholder="$t('dsp.selectPreset', 'Preset')"
          variant="minimal"
          :disabled="dspStore.isLoading"
          @change="handlePresetChange"
        />

        <!-- Save preset button -->
        <IconButton
          v-if="isDspEnabled && dspStore.isConnected"
          icon="plus"
          :variant="iconVariant"
          :disabled="dspStore.isLoading || isSaving"
          @click="openSaveDialog"
        />

        <!-- Delete preset button -->
        <IconButton
          v-if="isDspEnabled && selectedPreset"
          icon="trash"
          :variant="iconVariant"
          :disabled="dspStore.isLoading || isDeleting"
          @click="handleDeletePreset"
        />

        <!-- Reset button -->
        <IconButton
          v-if="isDspEnabled"
          icon="reset"
          :variant="iconVariant"
          :disabled="dspStore.isResetting"
          @click="handleResetAll"
        />

        <!-- DSP Enable/Disable toggle -->
        <Toggle
          v-model="isDspEnabled"
          :disabled="isToggling"
          @change="handleDspToggle"
        />
      </template>
    </ModalHeader>

    <!-- Main content -->
    <div class="content-wrapper">
      <Transition name="fade-slide" mode="out-in">
        <!-- MESSAGE: DSP disabled -->
        <MessageContent
          v-if="!isDspEnabled"
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

          <!-- Frequency Response Graph -->
          <FrequencyResponseGraph
            v-if="dspStore.isConnected && dspStore.filtersLoaded"
            :filters="dspStore.filters"
            :sample-rate="dspStore.sampleRate || 48000"
            :is-mobile="isMobile"
          />

          <!-- Parametric EQ -->
          <ParametricEQ
            v-if="dspStore.isConnected"
            :filters="dspStore.filters"
            :filters-loaded="dspStore.filtersLoaded"
            :disabled="dspStore.isUpdating"
            :is-mobile="isMobile"
            @update:filter="handleFilterUpdate"
            @change="handleFilterChange"
          />

          <!-- Quick Presets -->
          <QuickPresets
            v-if="dspStore.isConnected"
            ref="quickPresetsRef"
            :disabled="dspStore.isUpdating"
            :is-mobile="isMobile"
            @apply="handleQuickPresetApply"
          />

          <!-- Advanced DSP (Compressor, Loudness, Delay) -->
          <AdvancedDsp v-if="dspStore.isConnected && showAdvanced" />

          <!-- Level Meters -->
          <LevelMeters v-if="dspStore.isConnected" />
        </div>
      </Transition>
    </div>

    <!-- Toggle Advanced -->
    <button
      v-if="isDspEnabled && dspStore.isConnected"
      class="toggle-advanced"
      @click="showAdvanced = !showAdvanced"
    >
      {{ showAdvanced ? $t('dsp.hideAdvanced', 'Hide Advanced') : $t('dsp.showAdvanced', 'Show Advanced') }}
    </button>

    <!-- Save Preset Dialog -->
    <PresetSaveDialog
      :is-open="showSaveDialog"
      :initial-name="selectedPreset"
      :saving="isSaving"
      @close="showSaveDialog = false"
      @save="handleSavePreset"
    />

    <!-- Linked Clients Dialog -->
    <LinkedClientsDialog
      :is-open="showLinkedClientsDialog"
      @close="showLinkedClientsDialog = false"
    />
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue';
import { useDspStore } from '@/stores/dspStore';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import useWebSocket from '@/services/websocket';
import ModalHeader from '@/components/ui/ModalHeader.vue';
import IconButton from '@/components/ui/IconButton.vue';
import Toggle from '@/components/ui/Toggle.vue';
import Dropdown from '@/components/ui/Dropdown.vue';
import MessageContent from '@/components/ui/MessageContent.vue';
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue';
import ParametricEQ from './ParametricEQ.vue';
import AdvancedDsp from './AdvancedDsp.vue';
import LevelMeters from './LevelMeters.vue';
import PresetSaveDialog from './PresetSaveDialog.vue';
import QuickPresets from './QuickPresets.vue';
import FrequencyResponseGraph from './FrequencyResponseGraph.vue';
import LinkedClientsDialog from './LinkedClientsDialog.vue';

const dspStore = useDspStore();
const unifiedStore = useUnifiedAudioStore();
const { on } = useWebSocket();

// Local state
const isDspEnabled = ref(true); // TODO: Connect to routing state when DSP replaces equalizer
const isToggling = ref(false);
const selectedPreset = ref('');
const selectedTargetId = ref('local');
const isMobile = ref(false);
const showAdvanced = ref(false);

// Preset save/delete state
const showSaveDialog = ref(false);
const isSaving = ref(false);
const isDeleting = ref(false);

// Linked clients dialog state
const showLinkedClientsDialog = ref(false);

// Quick presets ref
const quickPresetsRef = ref(null);

let unsubscribeFunctions = [];

// === COMPUTED ===
const presetOptions = computed(() => {
  return dspStore.presets.map(name => ({
    value: name,
    label: name
  }));
});

const targetOptions = computed(() => {
  return dspStore.availableTargets.map(target => ({
    value: target.id,
    label: target.name
  }));
});

// Check if current target is linked to other clients
const isTargetLinked = computed(() => {
  return dspStore.isClientLinked(selectedTargetId.value);
});

// === MOBILE DETECTION ===
function updateMobileStatus() {
  const aspectRatio = window.innerWidth / window.innerHeight;
  isMobile.value = aspectRatio <= 4 / 3;
}

// === DSP TOGGLE ===
async function handleDspToggle(enabled) {
  const previousState = isDspEnabled.value;
  isDspEnabled.value = enabled;
  isToggling.value = true;

  try {
    // TODO: Implement DSP enable/disable via routing service
    // For now, just load/cleanup the store
    if (enabled) {
      await dspStore.loadStatus();
    } else {
      dspStore.cleanup();
    }
  } catch (error) {
    console.error('Error toggling DSP:', error);
    isDspEnabled.value = previousState;
  } finally {
    isToggling.value = false;
  }
}

// === FILTER UPDATES ===
function handleFilterUpdate({ id, field, value }) {
  dspStore.updateFilter(id, field, value);
}

function handleFilterChange({ id, field, value }) {
  dspStore.finalizeFilterUpdate(id);
}

// === RESET ===
async function handleResetAll() {
  await dspStore.resetAllFilters();
}

// === PRESET HANDLING ===
async function handlePresetChange(presetName) {
  if (presetName) {
    await dspStore.loadPreset(presetName);
  }
}

function openSaveDialog() {
  showSaveDialog.value = true;
}

async function handleSavePreset(name) {
  isSaving.value = true;
  try {
    await dspStore.savePreset(name);
    selectedPreset.value = name;
    showSaveDialog.value = false;
  } catch (error) {
    console.error('Error saving preset:', error);
  } finally {
    isSaving.value = false;
  }
}

async function handleDeletePreset() {
  if (!selectedPreset.value) return;

  // Simple confirmation
  const confirmed = window.confirm(`Delete preset "${selectedPreset.value}"?`);
  if (!confirmed) return;

  isDeleting.value = true;
  try {
    await dspStore.deletePreset(selectedPreset.value);
    selectedPreset.value = '';
  } catch (error) {
    console.error('Error deleting preset:', error);
  } finally {
    isDeleting.value = false;
  }
}

// === QUICK PRESETS ===
async function handleQuickPresetApply(gains) {
  // Apply each filter gain value
  for (let i = 0; i < dspStore.filters.length && i < gains.length; i++) {
    const filter = dspStore.filters[i];
    if (filter.gain !== gains[i]) {
      dspStore.updateFilter(filter.id, 'gain', gains[i]);
      await dspStore.finalizeFilterUpdate(filter.id);
    }
  }
  // Clear saved preset selection since we're using a quick preset
  selectedPreset.value = '';
}

// === TARGET HANDLING ===
async function handleTargetChange(targetId) {
  if (targetId) {
    await dspStore.selectTarget(targetId);
  }
}

// Watch for active preset changes
watch(() => dspStore.activePreset, (newPreset) => {
  selectedPreset.value = newPreset || '';
});

// Watch for selected target changes
watch(() => dspStore.selectedTarget, (newTarget) => {
  selectedTargetId.value = newTarget || 'local';
});

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
  selectedPreset.value = event.data.name || '';
}

// === LIFECYCLE ===
onMounted(async () => {
  updateMobileStatus();
  window.addEventListener('resize', updateMobileStatus);

  // Initialize filters
  dspStore.initializeFilters();

  // Load available DSP targets (Milo + clients)
  await dspStore.loadTargets();
  selectedTargetId.value = dspStore.selectedTarget || 'local';

  // Load DSP status if enabled
  if (isDspEnabled.value) {
    await dspStore.loadStatus();
    selectedPreset.value = dspStore.activePreset || '';
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
    on('dsp', 'links_changed', (e) => dspStore.handleLinksChanged(e))
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

.toggle-advanced {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-02) var(--space-03);
  border: none;
  border-radius: var(--radius-04);
  background: var(--color-background-neutral);
  color: var(--color-text-secondary);
  font-size: 13px;
  cursor: pointer;
  transition: all var(--transition-fast);
}

.toggle-advanced:hover {
  background: var(--color-border);
  color: var(--color-text);
}

/* Mobile adjustments */
@media (max-aspect-ratio: 4/3) {
  .dsp-modal {
    gap: var(--space-02);
  }
}
</style>
