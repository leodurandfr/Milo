<!-- frontend/src/components/equalizer/EqualizerModal.vue -->
<template>
  <div class="equalizer-modal">
    <!-- Header with toggle -->
    <ModalHeader :title="$t('equalizer.title')">
      <template #actions="{ iconVariant }">
        <IconButton v-if="isEqualizerEnabled" icon="reset" :variant="iconVariant" :disabled="equalizerStore.isResetting"
          @click="handleResetAllBands" />
        <Toggle v-model="isEqualizerEnabled"
          :disabled="unifiedStore.systemState.transitioning || isEqualizerToggling" @change="handleEqualizerToggle" />
      </template>
    </ModalHeader>

    <!-- Main content -->
    <div class="content-wrapper">
      <!-- Single Transition for both states -->
      <Transition name="fade-slide" mode="out-in">
        <!-- MESSAGE: Equalizer disabled -->
        <MessageContent v-if="!isEqualizerEnabled" key="message" icon="equalizer" :title="$t('equalizer.disabled')" />

        <!-- EQUALIZER: Controls -->
        <div v-else key="controls" class="equalizer-controls">
          <RangeSliderEqualizer v-for="band in equalizerStore.bands" :key="band.id" v-model="band.value"
            :label="band.display_name" :orientation="sliderOrientation" :min="0" :max="100" :step="1" unit="%"
            :disabled="equalizerStore.isUpdating || !equalizerStore.bandsLoaded"
            :class="{ 'slider-loading': !equalizerStore.bandsLoaded }" @input="handleBandInput(band.id, $event)"
            @change="handleBandChange(band.id, $event)" />
        </div>
      </Transition>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useEqualizerStore } from '@/stores/equalizerStore';
import useWebSocket from '@/services/websocket';
import ModalHeader from '@/components/ui/ModalHeader.vue';
import IconButton from '@/components/ui/IconButton.vue';
import Toggle from '@/components/ui/Toggle.vue';
import RangeSliderEqualizer from './RangeSliderEqualizer.vue';
import MessageContent from '@/components/ui/MessageContent.vue';

const unifiedStore = useUnifiedAudioStore();
const equalizerStore = useEqualizerStore();
const { on } = useWebSocket();

// Local state for toggling and optimistic UI
const isEqualizerToggling = ref(false);
const localEqualizerEnabled = ref(unifiedStore.systemState.equalizer_enabled);

// UI states
const isMobile = ref(false);

let unsubscribeFunctions = [];

// Computed
const isEqualizerEnabled = computed({
  get: () => localEqualizerEnabled.value,
  set: (value) => { localEqualizerEnabled.value = value; }
});
const sliderOrientation = computed(() => isMobile.value ? 'horizontal' : 'vertical');

// === MOBILE DETECTION ===
function updateMobileStatus() {
  const aspectRatio = window.innerWidth / window.innerHeight;
  isMobile.value = aspectRatio <= 4 / 3;
}

// === BAND MANAGEMENT ===
function handleBandInput(bandId, value) {
  equalizerStore.updateBand(bandId, value);
}

function handleBandChange(bandId, value) {
  equalizerStore.finalizeBandUpdate(bandId, value);
}

async function handleResetAllBands() {
  await equalizerStore.resetAllBands();
}

async function handleEqualizerToggle(enabled) {
  // Optimistic update: change the UI immediately
  const previousState = localEqualizerEnabled.value;
  localEqualizerEnabled.value = enabled;
  isEqualizerToggling.value = true;

  try {
    // Launch the API call in the background
    const success = await unifiedStore.setEqualizerEnabled(enabled);

    if (!success) {
      // On failure, revert to previous state
      localEqualizerEnabled.value = previousState;
      isEqualizerToggling.value = false;
    }
    // On success, the watcher will sync and unlock
  } catch (error) {
    // On error, revert to previous state
    console.error('Error toggling equalizer:', error);
    localEqualizerEnabled.value = previousState;
    isEqualizerToggling.value = false;
  }
}

// === WEBSOCKET HANDLERS ===
function handleEqualizerUpdate(event) {
  equalizerStore.handleBandChanged(event);
  equalizerStore.handleReset(event);
}

function handleEqualizerEnabling() {
  isEqualizerToggling.value = true;
}

function handleEqualizerDisabling() {
  isEqualizerToggling.value = true;
}

// === WATCHER TO SYNC WITH BACKEND ===
// Watcher to synchronize with the backend via WebSocket
let lastStoreState = null; // Will be initialized on the first tick
const watcherInterval = setInterval(() => {
  const currentStoreState = unifiedStore.systemState.equalizer_enabled;

  // Initialize lastStoreState on first pass
  if (lastStoreState === null) {
    lastStoreState = currentStoreState;
    return;
  }

  // Detect change in the store (backend confirmation via WebSocket)
  if (lastStoreState !== currentStoreState) {
    lastStoreState = currentStoreState;

    // Sync local state with the store
    localEqualizerEnabled.value = currentStoreState;

    // Unlock the toggle
    isEqualizerToggling.value = false;

    // Handle equalizer data
    if (currentStoreState) {
      // Activation: load bands
      equalizerStore.bandsLoaded = false;

      nextTick(async () => {
        await equalizerStore.loadBands();
        equalizerStore.bandsLoaded = true;
      });
    } else {
      // Deactivation: clean up
      equalizerStore.bandsLoaded = false;
      equalizerStore.cleanup();
    }
  }
}, 100);

// === LIFECYCLE ===
onMounted(async () => {
  updateMobileStatus();
  window.addEventListener('resize', updateMobileStatus);

  // Initialize bands immediately
  equalizerStore.initializeBands();

  // If the equalizer is already enabled, load the data
  if (localEqualizerEnabled.value) {
    equalizerStore.bandsLoaded = false;
    await equalizerStore.loadBands();
    equalizerStore.bandsLoaded = true;
  }

  unsubscribeFunctions.push(
    on('equalizer', 'band_changed', handleEqualizerUpdate),
    on('equalizer', 'reset', handleEqualizerUpdate),
    on('routing', 'equalizer_enabling', handleEqualizerEnabling),
    on('routing', 'equalizer_disabling', handleEqualizerDisabling)
  );
});

onUnmounted(() => {
  window.removeEventListener('resize', updateMobileStatus);
  unsubscribeFunctions.forEach(unsubscribe => unsubscribe());
  clearInterval(watcherInterval);
  equalizerStore.cleanup();
});
</script>

<style scoped>
.equalizer-modal {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

.content-wrapper {
  display: flex;
  flex-direction: column;
  position: relative;
}

.equalizer-controls {
  background: var(--color-background-neutral);
  border-radius: var(--radius-06);
  display: flex;
  justify-content: space-between;
  gap: var(--space-02);
  padding: var(--space-05);
  overflow-x: auto;
}

/* Loading animation for sliders */
.equalizer-controls :deep(.range-slider) {
  opacity: 1;
  transition: opacity 300ms ease;
}

.equalizer-controls .slider-loading :deep(.range-slider) {
  opacity: 0.5;
}

/* Exception pour le CircularIcon dark */
:deep(.circular-icon.circular-icon--dark) {
  width: 40px;
  height: 40px;
}

:deep(.circular-icon.circular-icon--dark svg) {
  width: 28px;
  height: 28px;
}

@media (max-aspect-ratio: 4/3) {
  .equalizer-controls {
    flex-direction: column;
    border-radius: var(--radius-05);
  }

  /* Exception pour le CircularIcon dark */
  :deep(.circular-icon.circular-icon--dark) {
    width: 32px;
    height: 32px;
  }

  :deep(.circular-icon.circular-icon--dark svg) {
    width: 20px;
    height: 20px;
  }
}
</style>