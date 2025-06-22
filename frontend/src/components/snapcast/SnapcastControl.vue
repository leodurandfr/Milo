<!-- frontend/src/components/snapcast/SnapcastControl.vue - Version OPTIM simplifiée -->
<template>
  <div class="snapcast-control">
    <h3>Snapcast Clients</h3>
    
    <div v-if="!isMultiroomActive" class="status-message">
      <span class="status-dot inactive"></span>
      Multiroom non actif
    </div>
    
    <div v-else-if="clients.length === 0" class="status-message">
      <span class="status-dot loading"></span>
      Aucun client connecté
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
      @client-updated="loadClients"
    />
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import useWebSocket from '@/services/websocket';
import axios from 'axios';
import SnapclientItem from './SnapclientItem.vue';
import SnapclientDetails from './SnapclientDetails.vue';

const unifiedStore = useUnifiedAudioStore();
const { on } = useWebSocket();

// État local ultra-simple
const clients = ref([]);
const selectedClient = ref(null);

// Références pour nettoyage
let unsubscribeFunctions = [];

const isMultiroomActive = computed(() => 
  unifiedStore.multiroomEnabled
);

// === FONCTIONS PRINCIPALES ===

async function loadClients() {
  if (!isMultiroomActive.value) {
    clients.value = [];
    return;
  }
  
  try {
    const response = await axios.get('/api/routing/snapcast/clients');
    clients.value = response.data.clients || [];
  } catch (error) {
    console.error('Error loading clients:', error);
    clients.value = [];
  }
}

// === GESTIONNAIRES D'ÉVÉNEMENTS ULTRA-SIMPLES ===

async function handleVolumeChange(clientId, volume) {
  try {
    await axios.post(`/api/routing/snapcast/client/${clientId}/volume`, { volume });
  } catch (error) {
    console.error('Error updating volume:', error);
  }
}

async function handleMuteToggle(clientId, muted) {
  try {
    await axios.post(`/api/routing/snapcast/client/${clientId}/mute`, { muted });
  } catch (error) {
    console.error('Error toggling mute:', error);
  }
}

function handleShowDetails(client) {
  selectedClient.value = client;
}

// === GESTIONNAIRES WEBSOCKET SNAPCAST TEMPS RÉEL ===

function handleClientConnected(event) {
  console.log('Client connected event:', event);
  const clientData = event.data.client;
  if (clientData && !clients.value.find(c => c.id === clientData.id)) {
    // Extraire les données essentielles pour éviter la pollution
    const newClient = {
      id: clientData.id,
      name: clientData.config?.name || clientData.host?.name || 'Unknown',
      volume: clientData.config?.volume?.percent || 0,
      muted: clientData.config?.volume?.muted || false,
      host: clientData.host?.name || 'Unknown',
      ip: clientData.host?.ip?.replace('::ffff:', '') || 'Unknown'
    };
    
    clients.value.push(newClient);
    console.log('Client connected and added:', newClient.name);
  }
}

function handleClientDisconnected(event) {
  const clientId = event.data.client_id;
  const clientIndex = clients.value.findIndex(c => c.id === clientId);
  
  if (clientIndex !== -1) {
    const clientName = clients.value[clientIndex].name;
    clients.value.splice(clientIndex, 1);
    console.log('Client disconnected:', clientName);
    
    // Fermer les détails si c'est le client sélectionné
    if (selectedClient.value?.id === clientId) {
      selectedClient.value = null;
    }
  }
}

function handleClientVolumeChanged(event) {
  const { client_id, volume, muted } = event.data;
  const client = clients.value.find(c => c.id === client_id);
  
  if (client) {
    // Le volume reçu est le volume réel (limites appliquées côté backend)
    client.volume = volume;
    if (muted !== undefined) {
      client.muted = muted;
    }
    console.log(`Client ${client.name} volume updated: ${volume}% (real volume)`);
  }
}

function handleClientNameChanged(event) {
  const { client_id, name } = event.data;
  const client = clients.value.find(c => c.id === client_id);
  
  if (client) {
    client.name = name;
    console.log(`Client ${client_id} name updated: ${name}`);
  }
}

function handleClientMuteChanged(event) {
  const { client_id, muted, volume } = event.data;
  const client = clients.value.find(c => c.id === client_id);
  
  if (client) {
    client.muted = muted;
    if (volume !== undefined) {
      client.volume = volume;
    }
    console.log(`Client ${client.name} mute updated: ${muted}`);
  }
}

// === LIFECYCLE ===

onMounted(async () => {
  // S'abonner aux événements Snapcast WebSocket temps réel AVANT de charger
  const subscriptions = [
    on('snapcast', 'client_connected', handleClientConnected),
    on('snapcast', 'client_disconnected', handleClientDisconnected),
    on('snapcast', 'client_volume_changed', handleClientVolumeChanged),
    on('snapcast', 'client_name_changed', handleClientNameChanged),
    on('snapcast', 'client_mute_changed', handleClientMuteChanged),
    // AJOUT : Écouter les changements d'état système pour le multiroom
    on('system', 'state_changed', (event) => {
      unifiedStore.updateState(event);
      // Si le multiroom vient d'être activé, charger les clients
      if (event.data.multiroom_changed && unifiedStore.multiroomEnabled) {
        loadClients();
      }
    })
  ];
  
  unsubscribeFunctions.push(...subscriptions);
  
  // Charger les clients initiaux APRÈS avoir configuré les abonnements
  if (isMultiroomActive.value) {
    await loadClients();
  }
});

onUnmounted(() => {
  // Nettoyer tous les abonnements WebSocket
  unsubscribeFunctions.forEach(unsubscribe => unsubscribe());
});

// Watcher pour le mode multiroom (si besoin)
import { watch } from 'vue';
watch(isMultiroomActive, async (newValue) => {
  if (newValue) {
    await loadClients();
  } else {
    clients.value = [];
    selectedClient.value = null;
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