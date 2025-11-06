<!-- frontend/src/components/equalizer/EqualizerModal.vue -->
<template>
  <div class="equalizer-modal">
    <div class="screen-main">
      <!-- Header avec toggle -->
      <ModalHeader :title="$t('equalizer.title')">
        <template #actions>
          <CircularIcon
            v-if="isEqualizerEnabled"
            icon="reset"
            variant="dark"
            :disabled="equalizerStore.isResetting"
            @click="handleResetAllBands"
          />
          <Toggle
            v-model="isEqualizerEnabled"
            variant="primary"
            :disabled="unifiedStore.systemState.transitioning || isEqualizerToggling"
            @change="handleEqualizerToggle"
          />
        </template>
      </ModalHeader>

      <!-- Contenu principal avec hauteur animée -->
      <div class="content-container" :style="{ height: containerHeight }">
        <div class="content-wrapper" ref="contentWrapperRef" :class="{ 'with-background': !isEqualizerEnabled }">
          <!-- MESSAGE : Égaliseur désactivé -->
          <Transition name="message">
            <div v-if="!isEqualizerEnabled" key="message" class="message-content">
              <Icon name="equalizer" :size="96" color="var(--color-background-glass)" />
              <p class="text-mono">{{ $t('equalizer.disabled') }}</p>
            </div>
          </Transition>

          <!-- EQUALIZER : Controls -->
          <Transition name="controls">
            <div v-if="isEqualizerEnabled" key="controls" class="equalizer-controls">
              <RangeSliderEqualizer
                v-for="band in equalizerStore.bands"
                :key="band.id"
                v-model="band.value"
                :label="band.display_name"
                :orientation="sliderOrientation"
                :min="0"
                :max="100"
                :step="1"
                unit="%"
                :disabled="equalizerStore.isUpdating || !equalizerStore.bandsLoaded"
                :class="{ 'slider-loading': !equalizerStore.bandsLoaded }"
                @input="handleBandInput(band.id, $event)"
                @change="handleBandChange(band.id, $event)"
              />
            </div>
          </Transition>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useEqualizerStore } from '@/stores/equalizerStore';
import useWebSocket from '@/services/websocket';
import ModalHeader from '@/components/ui/ModalHeader.vue';
import CircularIcon from '@/components/ui/CircularIcon.vue';
import Toggle from '@/components/ui/Toggle.vue';
import RangeSliderEqualizer from './RangeSliderEqualizer.vue';
import Icon from '@/components/ui/Icon.vue';

const unifiedStore = useUnifiedAudioStore();
const equalizerStore = useEqualizerStore();
const { on } = useWebSocket();

// État local pour le toggling et l'UI optimiste
const isEqualizerToggling = ref(false);
const localEqualizerEnabled = ref(false); // État local pour l'UI instantanée

// UI states
const isMobile = ref(false);
const contentWrapperRef = ref(null);
const containerHeight = ref('auto');

let unsubscribeFunctions = [];
let resizeObserver = null;

// Computed
const isEqualizerEnabled = computed({
  get: () => localEqualizerEnabled.value,
  set: (value) => { localEqualizerEnabled.value = value; }
});
const sliderOrientation = computed(() => isMobile.value ? 'horizontal' : 'vertical');

// === DÉTECTION MOBILE ===
function updateMobileStatus() {
  const aspectRatio = window.innerWidth / window.innerHeight;
  isMobile.value = aspectRatio <= 4 / 3;
}

// === RESIZE OBSERVER ===
let isFirstResize = true;

function setupResizeObserver() {
  if (resizeObserver) {
    resizeObserver.disconnect();
  }

  resizeObserver = new ResizeObserver(entries => {
    if (entries[0]) {
      const newHeight = entries[0].contentRect.height;

      // Première fois : initialiser sans transition
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

// === GESTION DES BANDES ===
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
  // Optimistic update : changer l'UI immédiatement
  const previousState = localEqualizerEnabled.value;
  localEqualizerEnabled.value = enabled;
  isEqualizerToggling.value = true;

  try {
    // Lancer l'appel API en arrière-plan
    const success = await unifiedStore.setEqualizerEnabled(enabled);

    if (!success) {
      // Si échec, revenir à l'état précédent
      localEqualizerEnabled.value = previousState;
      isEqualizerToggling.value = false;
    }
    // Si succès, le watcher se chargera de synchroniser et débloquer
  } catch (error) {
    // Si erreur, revenir à l'état précédent
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

// === WATCHER POUR SYNCHRONISER AVEC LE BACKEND ===
// Watcher pour synchroniser avec le backend via WebSocket
let lastStoreState = null; // Sera initialisé au premier tick
const watcherInterval = setInterval(() => {
  const currentStoreState = unifiedStore.systemState.equalizer_enabled;

  // Initialiser lastStoreState au premier passage
  if (lastStoreState === null) {
    lastStoreState = currentStoreState;
    return;
  }

  // Détecter changement dans le store (confirmation backend via WebSocket)
  if (lastStoreState !== currentStoreState) {
    lastStoreState = currentStoreState;

    // Synchroniser l'état local avec le store
    localEqualizerEnabled.value = currentStoreState;

    // Débloquer le toggle
    isEqualizerToggling.value = false;

    // Gérer les données de l'égaliseur
    if (currentStoreState) {
      // Activation : charger les bandes
      equalizerStore.bandsLoaded = false;

      nextTick(async () => {
        await equalizerStore.loadBands();
        equalizerStore.bandsLoaded = true;
      });
    } else {
      // Désactivation : nettoyer
      equalizerStore.bandsLoaded = false;
      equalizerStore.cleanup();
    }
  }
}, 100);

// === LIFECYCLE ===
onMounted(async () => {
  updateMobileStatus();
  window.addEventListener('resize', updateMobileStatus);

  // Initialiser l'état local AVANT de charger
  localEqualizerEnabled.value = unifiedStore.systemState.equalizer_enabled;

  // Initialiser les bandes immédiatement
  equalizerStore.initializeBands();

  // Si l'égaliseur est déjà activé, charger les données
  if (localEqualizerEnabled.value) {
    equalizerStore.bandsLoaded = false;
    await equalizerStore.loadBands();
    equalizerStore.bandsLoaded = true;
  }

  // Setup ResizeObserver après le prochain tick pour que la ref soit disponible
  await nextTick();
  setupResizeObserver();

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

  if (resizeObserver) {
    resizeObserver.disconnect();
  }

  equalizerStore.cleanup();
});
</script>

<style scoped>
.equalizer-modal {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.screen-main {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
  height: 100%;
  min-height: 0;
}

.content-container {
  transition: height var(--transition-spring);
  overflow: visible;
}

.content-wrapper {
  display: flex;
  flex-direction: column;
  overflow: visible;
  border-radius: var(--radius-04);
  transition: background 400ms ease;
  position: relative;
}

.content-wrapper.with-background {
  background: var(--color-background-neutral);
}

.message-content {
  display: flex;
  min-height: 232px;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  gap: var(--space-04);
  padding: var(--space-05);
}

.message-content .text-mono {
  text-align: center;
  color: var(--color-text-secondary);
}

.equalizer-controls {
  background: var(--color-background-neutral);
  border-radius: var(--radius-04);
  display: flex;
  justify-content: space-between;
  gap: var(--space-02);
  padding: var(--space-05);
  overflow-x: auto;
}

/* Transitions pour message */
.message-enter-active {
  transition: opacity 300ms ease, transform 300ms ease;
}

.message-leave-active {
  transition: opacity 300ms ease, transform 300ms ease;
  position: absolute;
  width: 100%;
}

.message-enter-from {
  opacity: 0;
  transform: translateY(12px);
}

.message-leave-to {
  opacity: 0;
  transform: translateY(-12px);
}

/* Transitions pour controls */
.controls-enter-active {
  transition: opacity 300ms ease 100ms, transform 300ms ease 100ms;
}

.controls-leave-active {
  transition: opacity 300ms ease, transform 300ms ease;
  position: absolute;
  width: 100%;
}

.controls-enter-from {
  opacity: 0;
  transform: translateY(12px);
}

.controls-leave-to {
  opacity: 0;
  transform: translateY(-12px);
}

/* Animation de chargement des sliders */
.equalizer-controls :deep(.range-slider) {
  opacity: 1;
  transition: opacity 300ms ease;
}

.equalizer-controls .slider-loading :deep(.range-slider) {
  opacity: 0.5;
}

@media (max-aspect-ratio: 4/3) {
  .message-content {
    min-height: 364px;
  }

  .equalizer-controls {
    flex-direction: column;
  }
}
</style>
