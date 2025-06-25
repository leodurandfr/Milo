<!-- frontend/src/views/EqualizerView.vue - Version OPTIM -->
<template>
  <div class="equalizer-view">
    <!-- Toggle Equalizer avec IconButton intégré -->
    <div class="toggle-wrapper">
      <div class="toggle-header">
        <h3>Audio Processing</h3>
        <div class="controls-wrapper">
          <IconButton
            v-if="isEqualizerEnabled"
            icon="🔄"
            :disabled="resetting"
            @click="resetAllBands"
          />
          <Toggle
            v-model="isEqualizerEnabled"
            :disabled="unifiedStore.isTransitioning"
            @change="handleEqualizerToggle"
          />
        </div>
      </div>
    </div>

    <!-- Contenu principal -->
    <div class="main-content">
      <div v-if="!isEqualizerEnabled" class="equalizer-disabled">
        <h2>Equalizer désactivé</h2>
        <p>Activez l'equalizer pour accéder aux réglages audio.</p>
      </div>

      <div v-else class="equalizer-active">
        <div v-if="equalizerStatus.available" class="equalizer-controls">
          <div v-if="loading" class="loading-state">
            <div class="loading-spinner"></div>
            <p>Chargement des bandes...</p>
          </div>
          
          <div v-else-if="bands.length === 0" class="no-bands">
            <p>Aucune bande disponible</p>
          </div>
          
          <div v-else class="bands-container">
            <RangeSliderEqualizer
              v-for="band in bands" 
              :key="band.id"
              v-model="band.value"
              :label="band.display_name"
              :min="0"
              :max="100"
              :step="1"
              unit="%"
              :disabled="updating"
              @input="handleBandInput(band.id, $event)"
              @change="handleBandChange(band.id, $event)"
            />
          </div>
        </div>

        <div v-else class="equalizer-unavailable">
          <h2>Equalizer indisponible</h2>
          <p>L'equalizer alsaequal n'est pas accessible.</p>
          <button @click="checkEqualizerStatus" class="retry-btn">
            Réessayer
          </button>
        </div>
      </div>
    </div>

  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import useWebSocket from '@/services/websocket';
import axios from 'axios';
import IconButton from '@/components/ui/IconButton.vue';
import Toggle from '@/components/ui/Toggle.vue';
import RangeSliderEqualizer from '@/components/equalizer/RangeSliderEqualizer.vue';

const unifiedStore = useUnifiedAudioStore();
const { on } = useWebSocket();

// État local
const loading = ref(false);
const updating = ref(false);
const resetting = ref(false);
const bands = ref([]);
const equalizerStatus = ref({ available: false });

// Gestion du throttling pour les bandes
const bandThrottleMap = new Map();
const THROTTLE_DELAY = 100;
const FINAL_DELAY = 300;

// Références pour nettoyage
let unsubscribeFunctions = [];

// État computed
const isEqualizerEnabled = computed(() => 
  unifiedStore.equalizerEnabled
);

// === MÉTHODES PRINCIPALES ===

async function loadEqualizerData() {
  if (!isEqualizerEnabled.value) return;
  
  loading.value = true;
  try {
    const [statusResponse, bandsResponse] = await Promise.all([
      axios.get('/api/equalizer/status'),
      axios.get('/api/equalizer/bands')
    ]);
    
    equalizerStatus.value = statusResponse.data;
    
    if (equalizerStatus.value.available) {
      bands.value = bandsResponse.data.bands || [];
    } else {
      bands.value = [];
    }
    
  } catch (error) {
    console.error('Error loading equalizer data:', error);
    equalizerStatus.value = { available: false };
    bands.value = [];
  } finally {
    loading.value = false;
  }
}

async function checkEqualizerStatus() {
  try {
    const response = await axios.get('/api/equalizer/available');
    equalizerStatus.value.available = response.data.available;
    
    if (response.data.available) {
      await loadEqualizerData();
    }
  } catch (error) {
    console.error('Error checking equalizer status:', error);
  }
}

// === GESTION DES BANDES AVEC THROTTLING ===

function handleBandInput(bandId, value) {
  // Mise à jour visuelle immédiate
  const band = bands.value.find(b => b.id === bandId);
  if (band) {
    band.value = value;
  }
  
  // Throttling des requêtes
  handleBandThrottled(bandId, value);
}

function handleBandChange(bandId, value) {
  // Requête finale
  sendBandRequest(bandId, value);
  clearThrottleForBand(bandId);
}

function handleBandThrottled(bandId, value) {
  const now = Date.now();
  let state = bandThrottleMap.get(bandId) || {};
  
  // Nettoyer les timeouts existants
  if (state.throttleTimeout) clearTimeout(state.throttleTimeout);
  if (state.finalTimeout) clearTimeout(state.finalTimeout);
  
  // Envoyer immédiatement si pas de requête récente
  if (!state.lastRequestTime || (now - state.lastRequestTime) >= THROTTLE_DELAY) {
    sendBandRequest(bandId, value);
    state.lastRequestTime = now;
  } else {
    // Programmer une requête throttlée
    state.throttleTimeout = setTimeout(() => {
      sendBandRequest(bandId, value);
      state.lastRequestTime = Date.now();
    }, THROTTLE_DELAY - (now - state.lastRequestTime));
  }
  
  // Programmer une requête finale
  state.finalTimeout = setTimeout(() => {
    sendBandRequest(bandId, value);
    state.lastRequestTime = Date.now();
  }, FINAL_DELAY);
  
  bandThrottleMap.set(bandId, state);
}

async function sendBandRequest(bandId, value) {
  try {
    const response = await axios.post(`/api/equalizer/band/${bandId}`, { 
      value 
    });
    
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
    const response = await axios.post('/api/equalizer/reset', { value: 60 });
    
    if (response.data.status === 'success') {
      // Mettre à jour l'affichage local
      bands.value.forEach(band => {
        band.value = 60;
      });
    } else {
      console.error('Failed to reset bands:', response.data.message);
    }
  } catch (error) {
    console.error('Error resetting bands:', error);
  } finally {
    resetting.value = false;
  }
}

// === GESTION TOGGLE ===

async function handleEqualizerToggle(enabled) {
  await unifiedStore.setEqualizerEnabled(enabled);
}

// === GESTION WEBSOCKET ===

function handleEqualizerUpdate(event) {
  if (event.data.band_changed) {
    const { band_id, value } = event.data;
    const band = bands.value.find(b => b.id === band_id);
    if (band && bandThrottleMap.size === 0) {
      band.value = value;
    }
  } else if (event.data.reset) {
    if (bandThrottleMap.size === 0) {
      bands.value.forEach(band => {
        band.value = event.data.reset_value;
      });
    }
  }
}

// === LIFECYCLE ===

onMounted(async () => {
  if (isEqualizerEnabled.value) {
    await loadEqualizerData();
  }
  
  // S'abonner aux événements equalizer
  const unsubscribe1 = on('equalizer', 'band_changed', handleEqualizerUpdate);
  const unsubscribe2 = on('equalizer', 'reset', handleEqualizerUpdate);
  const unsubscribe3 = on('system', 'state_changed', (event) => {
    unifiedStore.updateState(event);
  });
  
  unsubscribeFunctions.push(unsubscribe1, unsubscribe2, unsubscribe3);
});

onUnmounted(() => {
  // Nettoyer les abonnements WebSocket
  unsubscribeFunctions.forEach(unsubscribe => unsubscribe());
  
  // Nettoyer tous les timeouts en cours
  bandThrottleMap.forEach(state => {
    if (state.throttleTimeout) clearTimeout(state.throttleTimeout);
    if (state.finalTimeout) clearTimeout(state.finalTimeout);
  });
  bandThrottleMap.clear();
});

// Watcher pour le mode equalizer
async function watchEqualizerState() {
  if (isEqualizerEnabled.value) {
    await loadEqualizerData();
  } else {
    bands.value = [];
    equalizerStatus.value = { available: false };
    
    // Nettoyer les états des requêtes
    bandThrottleMap.forEach(state => {
      if (state.throttleTimeout) clearTimeout(state.throttleTimeout);
      if (state.finalTimeout) clearTimeout(state.finalTimeout);
    });
    bandThrottleMap.clear();
  }
}

// Watcher manuel via computed
let lastEqualizerState = isEqualizerEnabled.value;
setInterval(() => {
  if (lastEqualizerState !== isEqualizerEnabled.value) {
    lastEqualizerState = isEqualizerEnabled.value;
    watchEqualizerState();
  }
}, 100);
</script>

<style scoped>
.equalizer-view {
  padding: 20px;
  width: 80%;
  margin: 0 auto;
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}

/* Toggle wrapper */
.toggle-wrapper {
  background: white;
  border: 1px solid #ddd;
  border-radius: 16px;
  padding: 16px;
  margin: 16px 0;
}

.toggle-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.toggle-header h3 {
  margin: 0;
  color: #333;
  font-size: 16px;
}

.controls-wrapper {
  display: flex;
  align-items: center;
  gap: 12px;
}

/* Contenu principal */
.main-content {
  flex: 1;
  margin-bottom: 20px;
}

.equalizer-disabled, .equalizer-unavailable {
  background: white;
  border: 1px solid #ddd;
  padding: 40px;
  text-align: center;
  color: #666;
}

.equalizer-disabled h2, .equalizer-unavailable h2 {
  margin: 0 0 16px 0;
  color: #333;
}

.equalizer-disabled p, .equalizer-unavailable p {
  margin: 0 0 16px 0;
  font-size: 14px;
}

.retry-btn {
  padding: 8px 16px;
  background: #2196F3;
  color: white;
  border: none;
  cursor: pointer;
}

.retry-btn:hover {
  background: #1976D2;
}

.equalizer-active {
  background: white;
  border: 1px solid #ddd;
  padding: 20px;
}

/* États de chargement */
.loading-state {
  text-align: center;
  padding: 40px 20px;
}

.loading-spinner {
  width: 30px;
  height: 30px;
  border: 3px solid #f3f3f3;
  border-top: 3px solid #2196F3;
  border-radius: 50%;
  animation: spin 1s linear infinite;
  margin: 0 auto 16px;
}

@keyframes spin {
  0% { transform: rotate(0deg); }
  100% { transform: rotate(360deg); }
}

.no-bands {
  text-align: center;
  padding: 20px;
  color: #666;
}

/* Conteneur des bandes */
.bands-container {
  display: flex;
  justify-content: space-between;
  align-items: end;
  gap: 8px;
  padding: 24px;
  background: white;
  border: 1px solid #dee2e6;
  border-radius: 16px;
  overflow-x: auto;
  flex: 1;        /* 👈 AJOUTER : Prend l'espace disponible */
  min-height: 0;  /* 👈 AJOUTER : Force la contrainte de hauteur */
}

/* Responsive */
@media (max-width: 600px) {
  .bands-container {
    gap: 6px;
    padding: 12px 8px;
  }
  
  .controls-wrapper {
    gap: 8px;
  }
}

@media (max-width: 400px) {
  .bands-container {
    gap: 4px;
    padding: 8px 4px;
  }
}
</style>