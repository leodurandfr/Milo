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
            :disabled="unifiedStore.isTransitioning"
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
const CACHE_KEY = 'equalizer_bands_cache';

// État local
const updating = ref(false);
const resetting = ref(false);
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
const isEqualizerEnabled = computed(() => unifiedStore.equalizerEnabled);
const sliderOrientation = computed(() => isMobile.value ? 'horizontal' : 'vertical');

// === CACHE MANAGEMENT ===
function loadCache() {
  try {
    const cached = localStorage.getItem(CACHE_KEY);
    if (!cached) return null;
    return JSON.parse(cached);
  } catch (error) {
    console.error('Error loading equalizer cache:', error);
    return null;
  }
}

function saveCache(bandsList) {
  try {
    const cacheData = bandsList.map(b => ({ id: b.id, value: b.value }));
    localStorage.setItem(CACHE_KEY, JSON.stringify(cacheData));
  } catch (error) {
    console.error('Error saving equalizer cache:', error);
  }
}

// === INITIALISATION DES BANDES ===
function initializeBands() {
  const cache = loadCache();

  if (cache && cache.length === 10) {
    // Utiliser le cache
    bands.value = STATIC_BANDS.map(staticBand => {
      const cachedBand = cache.find(c => c.id === staticBand.id);
      return {
        ...staticBand,
        value: cachedBand ? cachedBand.value : DEFAULT_VALUE
      };
    });
  } else {
    // Valeurs par défaut
    bands.value = STATIC_BANDS.map(band => ({
      ...band,
      value: DEFAULT_VALUE
    }));
  }
}

// === LOAD EQUALIZER DATA ===
async function loadEqualizerData() {
  if (!isEqualizerEnabled.value) return;

  bandsLoaded.value = false;

  try {
    const [statusResponse, bandsResponse] = await Promise.all([
      axios.get('/api/equalizer/status'),
      axios.get('/api/equalizer/bands')
    ]);

    if (statusResponse.data.available && bandsResponse.data.bands) {
      const apiBands = bandsResponse.data.bands;

      // Animer vers les vraies valeurs
      bands.value = bands.value.map(band => {
        const apiBand = apiBands.find(b => b.id === band.id);
        return {
          ...band,
          value: apiBand ? apiBand.value : band.value
        };
      });

      saveCache(bands.value);
    }
  } catch (error) {
    console.error('Error loading equalizer data:', error);
  } finally {
    // Petit délai pour l'animation CSS
    setTimeout(() => {
      bandsLoaded.value = true;
    }, 50);
  }
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
  saveCache(bands.value);
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
      saveCache(bands.value);
    }
  } catch (error) {
    console.error('Error resetting bands:', error);
  } finally {
    resetting.value = false;
  }
}

async function handleEqualizerToggle(enabled) {
  await unifiedStore.setEqualizerEnabled(enabled);
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

// Watcher equalizer state
let lastEqualizerState = isEqualizerEnabled.value;
const watcherInterval = setInterval(() => {
  if (lastEqualizerState !== isEqualizerEnabled.value) {
    lastEqualizerState = isEqualizerEnabled.value;
    if (isEqualizerEnabled.value) {
      initializeBands();
      loadEqualizerData();
    } else {
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

  // Initialiser les bandes immédiatement
  initializeBands();

  await fetchEqualizerState();

  // Setup ResizeObserver après le prochain tick pour que la ref soit disponible
  await nextTick();
  setupResizeObserver();

  unsubscribeFunctions.push(
    on('equalizer', 'band_changed', handleEqualizerUpdate),
    on('equalizer', 'reset', handleEqualizerUpdate)
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
.slider-loading {
  opacity: 0.5;
  transition: opacity 200ms ease-out;
}

.slider-loading:not(.slider-loading) {
  opacity: 1;
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