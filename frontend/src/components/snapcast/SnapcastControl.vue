<!-- frontend/src/components/snapcast/SnapcastControl.vue - Version nettoyée -->
<template>
  <div class="snapcast-control">
    <h3>Snapcast Clients</h3>
    
    <div v-if="!isMultiroomActive" class="status-message">
      <span class="status-dot inactive"></span>
      Multiroom non actif
    </div>
    
    <div v-else-if="clients.length === 0" class="status-message">
      <span class="status-dot loading"></span>
      {{ loading ? 'Chargement...' : 'Aucun client connecté' }}
    </div>
    
    <div v-else class="clients-list">
      <SnapclientItem
        v-for="client in clients"
        :key="client.id"
        :client="client"
        @volume-change="handleVolumeChange"
        @mute-toggle="handleMuteToggle"
        @show-details="handleShowDetails"
      />
    </div>

    <!-- Modal détails client -->
    <SnapclientDetails
      v-if="selectedClient"
      :client="selectedClient"
      @close="selectedClient = null"
      @client-updated="fetchClients"
    />
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import useWebSocket from '@/services/websocket';
import axios from 'axios';
import SnapclientItem from './SnapclientItem.vue';
import SnapclientDetails from './SnapclientDetails.vue';

const unifiedStore = useUnifiedAudioStore();
const { on } = useWebSocket();

// État local optimisé
const clients = ref([]);
const loading = ref(false);
const selectedClient = ref(null);

// Gestion du throttling optimisée avec Map
const volumeThrottleMap = new Map();
const THROTTLE_DELAY = 200;
const FINAL_DELAY = 500;

// Références pour nettoyage
let unsubscribeFunctions = [];

const isMultiroomActive = computed(() => 
  unifiedStore.multiroomEnabled
);

// === MÉTHODES PRINCIPALES ===

async function fetchClients() {
  if (!isMultiroomActive.value) {
    clients.value = [];
    return;
  }
  
  loading.value = true;
  try {
    const response = await axios.get('/api/routing/snapcast/clients');
    clients.value = response.data.clients || [];
  } catch (error) {
    console.error('Error fetching clients:', error);
    clients.value = [];
  } finally {
    loading.value = false;
  }
}

// === GESTIONNAIRES D'ÉVÉNEMENTS OPTIMISÉS ===

function handleVolumeChange(clientId, volume, type = 'input') {
  if (type === 'input') {
    handleVolumeThrottled(clientId, volume);
  } else {
    // type === 'change' - valeur finale
    sendVolumeRequest(clientId, volume);
    clearThrottleForClient(clientId);
  }
}

function handleVolumeThrottled(clientId, volume) {
  const now = Date.now();
  let state = volumeThrottleMap.get(clientId) || {};
  
  // Nettoyer les timeouts existants
  if (state.throttleTimeout) clearTimeout(state.throttleTimeout);
  if (state.finalTimeout) clearTimeout(state.finalTimeout);
  
  // Envoyer immédiatement si pas de requête récente
  if (!state.lastRequestTime || (now - state.lastRequestTime) >= THROTTLE_DELAY) {
    sendVolumeRequest(clientId, volume);
    state.lastRequestTime = now;
  } else {
    // Programmer une requête throttlée
    state.throttleTimeout = setTimeout(() => {
      sendVolumeRequest(clientId, volume);
      state.lastRequestTime = Date.now();
    }, THROTTLE_DELAY - (now - state.lastRequestTime));
  }
  
  // Toujours programmer une requête finale
  state.finalTimeout = setTimeout(() => {
    sendVolumeRequest(clientId, volume);
    state.lastRequestTime = Date.now();
  }, FINAL_DELAY);
  
  volumeThrottleMap.set(clientId, state);
}

async function sendVolumeRequest(clientId, volume) {
  try {
    const response = await axios.post(`/api/routing/snapcast/client/${clientId}/volume`, { 
      volume 
    });
    
    if (response.data.status !== 'success') {
      console.error('Failed to update volume:', response.data.message);
    }
  } catch (error) {
    console.error('Error updating volume:', error);
  }
}

function clearThrottleForClient(clientId) {
  const state = volumeThrottleMap.get(clientId);
  if (state) {
    if (state.throttleTimeout) clearTimeout(state.throttleTimeout);
    if (state.finalTimeout) clearTimeout(state.finalTimeout);
    volumeThrottleMap.delete(clientId);
  }
}

async function handleMuteToggle(clientId, muted) {
  try {
    const response = await axios.post(`/api/routing/snapcast/client/${clientId}/mute`, { 
      muted 
    });
    
    if (response.data.status !== 'success') {
      console.error('Failed to toggle mute:', response.data.message);
    }
  } catch (error) {
    console.error('Error toggling mute:', error);
  }
}

function handleShowDetails(client) {
  selectedClient.value = client;
}

// === GESTION WEBSOCKET (SNAPCAST UNIQUEMENT) ===

function handleSnapcastUpdate(event) {
  if (event.data.snapcast_update && isMultiroomActive.value) {
    console.log('Received Snapcast update via WebSocket, refreshing clients');
    
    // Éviter le refresh si des modifications locales sont en cours
    if (volumeThrottleMap.size === 0) {
      fetchClients();
    } else {
      console.log('Skipping refresh due to local volume changes in progress');
    }
  }
}

// === LIFECYCLE ===

onMounted(async () => {
  if (isMultiroomActive.value) {
    await fetchClients();
  }
  
  // S'abonner uniquement aux événements Snapcast (pas volume)
  const unsubscribe = on('system', 'state_changed', (event) => {
    if (event.source === 'snapcast') {
      handleSnapcastUpdate(event);
    }
  });
  
  unsubscribeFunctions.push(unsubscribe);
});

onUnmounted(() => {
  // Nettoyer les abonnements WebSocket
  unsubscribeFunctions.forEach(unsubscribe => unsubscribe());
  
  // Nettoyer tous les timeouts en cours
  volumeThrottleMap.forEach(state => {
    if (state.throttleTimeout) clearTimeout(state.throttleTimeout);
    if (state.finalTimeout) clearTimeout(state.finalTimeout);
  });
  volumeThrottleMap.clear();
});

// Watcher pour le mode multiroom
watch(isMultiroomActive, async (newValue) => {
  if (newValue) {
    await fetchClients();
  } else {
    clients.value = [];
    selectedClient.value = null;
    
    // Nettoyer les états des requêtes
    volumeThrottleMap.forEach(state => {
      if (state.throttleTimeout) clearTimeout(state.throttleTimeout);
      if (state.finalTimeout) clearTimeout(state.finalTimeout);
    });
    volumeThrottleMap.clear();
  }
});
</script>

<style scoped>
.snapcast-control {
  background: white;
  border: 1px solid #ddd;
  border-radius: 8px;
  padding: 16px;
  margin: 16px 0;
}

.snapcast-control h3 {
  margin: 0 0 16px 0;
  color: #333;
  font-size: 16px;
}

.status-message {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px;
  border-radius: 4px;
  font-size: 14px;
  background: #f5f5f5;
  color: #666;
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #ccc;
}

.status-dot.inactive { 
  background: #999; 
}

.status-dot.loading { 
  background: #17a2b8;
  animation: pulse 1.5s infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}

.clients-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
</style>