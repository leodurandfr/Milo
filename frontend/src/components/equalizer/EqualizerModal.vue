<!-- frontend/src/components/equalizer/EqualizerModal.vue -->
<template>
  <div class="equalizer-modal">
    <div class="screen-main">
      <!-- Header avec toggle -->
      <ModalHeader :title="$t('equalizer.title')">
        <template #actions>
          <IconButton
            v-if="isEqualizerEnabled"
            icon="reset"
            variant="dark"
            :disabled="resetting"
            @click="resetAllBands"
          />
          <Toggle
            v-model="isEqualizerEnabled"
            variant="primary"
            :disabled="unifiedStore.isTransitioning || isEqualizerToggling"
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
                v-for="band in bands"
                :key="band.id"
                v-model="band.value"
                :label="band.display_name"
                :orientation="sliderOrientation"
                :min="0"
                :max="100"
                :step="1"
                unit="%"
                :disabled="updating || !bandsLoaded"
                :class="{ 'slider-loading': !bandsLoaded }"
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
import useWebSocket from '@/services/websocket';
import axios from 'axios';
import ModalHeader from '@/components/ui/ModalHeader.vue';
import IconButton from '@/components/ui/IconButton.vue';
import Toggle from '@/components/ui/Toggle.vue';
import RangeSliderEqualizer from './RangeSliderEqualizer.vue';
import Icon from '@/components/ui/Icon.vue';

const unifiedStore = useUnifiedAudioStore();
const { on } = useWebSocket();

// État local pour le toggling et l'UI optimiste
const isEqualizerToggling = ref(false);
const localEqualizerEnabled = ref(false); // État local pour l'UI instantanée

// === CONSTANTES ===
const STATIC_BANDS = [
  { id: "00", freq: "31 Hz", display_name: "31" },
  { id: "01", freq: "63 Hz", display_name: "63" },
  { id: "02", freq: "125 Hz", display_name: "125" },
  { id: "03", freq: "250 Hz", display_name: "250" },
  { id: "04", freq: "500 Hz", display_name: "500" },
  { id: "05", freq: "1 kHz", display_name: "1K" },
  { id: "06", freq: "2 kHz", display_name: "2K" },
  { id: "07", freq: "4 kHz", display_name: "4K" },
  { id: "08", freq: "8 kHz", display_name: "8K" },
  { id: "09", freq: "16 kHz", display_name: "16K" }
];

const DEFAULT_VALUE = 66;

// État local
const updating = ref(false);
const resetting = ref(false);
const isToggling = ref(false);
const bands = ref([]);
const bandsLoaded = ref(false);
const isMobile = ref(false);
const contentWrapperRef = ref(null);
const containerHeight = ref('auto');

// Gestion du throttling
const bandThrottleMap = new Map();
const THROTTLE_DELAY = 100;
const FINAL_DELAY = 300;

let unsubscribeFunctions = [];
let resizeObserver = null;

// Computed
const isEqualizerEnabled = computed({
  get: () => localEqualizerEnabled.value,
  set: (value) => { localEqualizerEnabled.value = value; } // Permet le v-model sur le Toggle
});
const sliderOrientation = computed(() => isMobile.value ? 'horizontal' : 'vertical');

// === INITIALISATION DES BANDES ===
function initializeBands() {
  // Initialiser avec les valeurs par défaut (seront remplacées par l'API)
  bands.value = STATIC_BANDS.map(band => ({
    ...band,
    value: DEFAULT_VALUE
  }));
}

// === LOAD EQUALIZER DATA ===
async function loadEqualizerData() {
  // Ne pas vérifier isEqualizerEnabled pour permettre le chargement au mount
  try {
    const [statusResponse, bandsResponse] = await Promise.all([
      axios.get('/api/equalizer/status'),
      axios.get('/api/equalizer/bands')
    ]);

    if (statusResponse.data.available && bandsResponse.data.bands) {
      const apiBands = bandsResponse.data.bands;

      // Mettre à jour vers les vraies valeurs depuis l'API (source de vérité unique)
      bands.value = bands.value.map(band => {
        const apiBand = apiBands.find(b => b.id === band.id);
        return {
          ...band,
          value: apiBand ? apiBand.value : band.value
        };
      });
    }
  } catch (error) {
    console.error('Error loading equalizer data:', error);
  }

  // Assurer que les contrôles sont visibles
  bandsLoaded.value = true;
}

// === FETCH INITIAL ===
async function fetchEqualizerState() {
  try {
    const routingResponse = await axios.get('/api/routing/status');
    const routingData = routingResponse.data;

    if (routingData.routing?.equalizer_enabled !== undefined) {
      unifiedStore.systemState.equalizer_enabled = routingData.routing.equalizer_enabled;
    }

    if (routingData.routing?.equalizer_enabled) {
      await loadEqualizerData();
    }
  } catch (error) {
    console.error('Error fetching equalizer state:', error);
  }
}

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
  const band = bands.value.find(b => b.id === bandId);
  if (band) band.value = value;
  handleBandThrottled(bandId, value);
}

function handleBandChange(bandId, value) {
  sendBandRequest(bandId, value);
  clearThrottleForBand(bandId);
  // Backend est la source de vérité, pas besoin de cache local
}

function handleBandThrottled(bandId, value) {
  const now = Date.now();
  let state = bandThrottleMap.get(bandId) || {};

  if (state.throttleTimeout) clearTimeout(state.throttleTimeout);
  if (state.finalTimeout) clearTimeout(state.finalTimeout);

  if (!state.lastRequestTime || (now - state.lastRequestTime) >= THROTTLE_DELAY) {
    sendBandRequest(bandId, value);
    state.lastRequestTime = now;
  } else {
    state.throttleTimeout = setTimeout(() => {
      sendBandRequest(bandId, value);
      state.lastRequestTime = Date.now();
    }, THROTTLE_DELAY - (now - state.lastRequestTime));
  }

  state.finalTimeout = setTimeout(() => {
    sendBandRequest(bandId, value);
    state.lastRequestTime = Date.now();
  }, FINAL_DELAY);

  bandThrottleMap.set(bandId, state);
}

async function sendBandRequest(bandId, value) {
  try {
    const response = await axios.post(`/api/equalizer/band/${bandId}`, { value });
    if (response.data.status !== 'success') {
      console.error('Failed to update band:', response.data.message);
    }
  } catch (error) {
    console.error('Error updating band:', error);
  }
}

function clearThrottleForBand(bandId) {
  const state = bandThrottleMap.get(bandId);
  if (state) {
    if (state.throttleTimeout) clearTimeout(state.throttleTimeout);
    if (state.finalTimeout) clearTimeout(state.finalTimeout);
    bandThrottleMap.delete(bandId);
  }
}

async function resetAllBands() {
  if (resetting.value) return;

  resetting.value = true;
  try {
    const response = await axios.post('/api/equalizer/reset', { value: 66 });
    if (response.data.status === 'success') {
      bands.value.forEach(band => { band.value = 66; });
      // Backend est la source de vérité, pas besoin de cache local
    }
  } catch (error) {
    console.error('Error resetting bands:', error);
  } finally {
    resetting.value = false;
  }
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

// === WEBSOCKET ===
function handleEqualizerUpdate(event) {
  if (event.data.band_changed) {
    const { band_id, value } = event.data;
    const band = bands.value.find(b => b.id === band_id);
    if (band && bandThrottleMap.size === 0) {
      band.value = value;
    }
  } else if (event.data.reset) {
    if (bandThrottleMap.size === 0) {
      bands.value.forEach(band => { band.value = event.data.reset_value; });
    }
  }
}

function handleEqualizerEnabling() {
  isEqualizerToggling.value = true;
}

function handleEqualizerDisabling() {
  isEqualizerToggling.value = true;
}

// Watcher pour synchroniser avec le backend via WebSocket
let lastStoreState = null; // Sera initialisé au premier tick
const watcherInterval = setInterval(() => {
  const currentStoreState = unifiedStore.equalizerEnabled;

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
      // Activation : garder les bandes existantes, juste recharger les vraies valeurs
      // (pas de réinitialisation pour éviter le tremblement visuel)
      bandsLoaded.value = false; // Opacité 0.5 pendant le chargement

      nextTick(async () => {
        await loadEqualizerData();
        bandsLoaded.value = true; // Opacité 1 quand chargé
      });
    } else {
      // Désactivation : nettoyer
      bandsLoaded.value = false;
      bandThrottleMap.forEach(state => {
        if (state.throttleTimeout) clearTimeout(state.throttleTimeout);
        if (state.finalTimeout) clearTimeout(state.finalTimeout);
      });
      bandThrottleMap.clear();
    }
  }
}, 100);

// === LIFECYCLE ===
onMounted(async () => {
  updateMobileStatus();
  window.addEventListener('resize', updateMobileStatus);

  // Nettoyer l'ancien cache localStorage (obsolète, backend est la source de vérité)
  try {
    localStorage.removeItem('equalizer_bands_cache');
  } catch (error) {
    // Ignore si échec
  }

  // Initialiser l'état local AVANT fetchEqualizerState pour que loadEqualizerData fonctionne
  localEqualizerEnabled.value = unifiedStore.equalizerEnabled;

  // Initialiser les bandes immédiatement
  initializeBands();

  // Si l'égaliseur est déjà activé, marquer comme loading pendant le fetch
  if (localEqualizerEnabled.value) {
    bandsLoaded.value = false;
  }

  await fetchEqualizerState();

  // Après le fetch, si égaliseur activé, marquer comme chargé
  if (localEqualizerEnabled.value) {
    bandsLoaded.value = true;
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

  bandThrottleMap.forEach(state => {
    if (state.throttleTimeout) clearTimeout(state.throttleTimeout);
    if (state.finalTimeout) clearTimeout(state.finalTimeout);
  });
  bandThrottleMap.clear();
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
  position: relative;
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