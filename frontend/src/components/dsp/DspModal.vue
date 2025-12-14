<!-- frontend/src/components/dsp/DspModal.vue -->
<!-- Main DSP control panel with parametric EQ and presets -->
<template>
  <div class="dsp-modal">
    <!-- Header with toggle only -->
    <ModalHeader :title="$t('dsp.title', 'DSP')">
      <template #actions="{ iconVariant }">
        <!-- Link clients button -->
        <IconButton
          v-if="isDspEnabled && hasMultipleTargets"
          icon="link"
          :variant="isTargetLinked ? 'brand' : iconVariant"
          :disabled="dspStore.isLoading"
          @click="showLinkedClientsDialog = true"
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

          <template v-if="dspStore.isConnected">
            <!-- Zone Tabs + Preset selector (first section) -->
            <ZoneTabs :disabled="dspStore.isUpdating" />

            <!-- Parametric EQ -->
            <ParametricEQ
              :filters="dspStore.filters"
              :filters-loaded="dspStore.filtersLoaded"
              :disabled="dspStore.isUpdating"
              :is-mobile="isMobile"
              @update:filter="handleFilterUpdate"
              @change="handleFilterChange"
            />

            <!-- Advanced DSP (Compressor, Loudness, Delay, Volume) - always visible -->
            <AdvancedDsp />

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
import { ref, computed, onMounted, onUnmounted } from 'vue';
import { useDspStore } from '@/stores/dspStore';
import useWebSocket from '@/services/websocket';
import ModalHeader from '@/components/ui/ModalHeader.vue';
import IconButton from '@/components/ui/IconButton.vue';
import Toggle from '@/components/ui/Toggle.vue';
import MessageContent from '@/components/ui/MessageContent.vue';
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue';
import ZoneTabs from './ZoneTabs.vue';
import ParametricEQ from './ParametricEQ.vue';
import AdvancedDsp from './AdvancedDsp.vue';
import LevelMeters from './LevelMeters.vue';
import LinkedClientsDialog from './LinkedClientsDialog.vue';

const dspStore = useDspStore();
const { on } = useWebSocket();

// Local state
const isDspEnabled = ref(true); // TODO: Connect to routing state when DSP replaces equalizer
const isToggling = ref(false);
const isMobile = ref(false);

// Linked clients dialog state
const showLinkedClientsDialog = ref(false);

let unsubscribeFunctions = [];

// === COMPUTED ===
const hasMultipleTargets = computed(() => dspStore.availableTargets.length > 1);

// Check if current target is linked to other clients
const isTargetLinked = computed(() => {
  return dspStore.isClientLinked(dspStore.selectedTarget);
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

  // Load available DSP targets (Milo + clients)
  await dspStore.loadTargets();

  // Load DSP status if enabled
  if (isDspEnabled.value) {
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

/* Mobile adjustments */
@media (max-aspect-ratio: 4/3) {
  .dsp-modal {
    gap: var(--space-02);
  }
}
</style>
