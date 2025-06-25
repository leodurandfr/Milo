<!-- frontend/src/components/snapcast/SnapcastControl.vue - Version 100% événementielle OPTIM -->
<template>


  <div v-if="!isMultiroomActive" class="status-message">
    <span class="status-dot inactive"></span>
    Multiroom non actif
  </div>

  <div v-else-if="clients.length === 0" class="status-message">
    <span class="status-dot loading"></span>
    Aucun client connecté
  </div>

  <div v-else class="clients-list">
    <SnapclientItem v-for="client in clients" :key="client.id" :client="client" @volume-change="handleVolumeChange"
      @mute-toggle="handleMuteToggle" @show-details="handleShowDetails" />
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useModalStore } from '@/stores/modalStore';
import useWebSocket from '@/services/websocket';
import axios from 'axios';
import SnapclientItem from './SnapclientItem.vue';

const unifiedStore = useUnifiedAudioStore();
const modalStore = useModalStore();
const { on } = useWebSocket();

// État local ultra-simple
const clients = ref([]);

// Références pour nettoyage
let unsubscribeFunctions = [];

const isMultiroomActive = computed(() =>
  unifiedStore.multiroomEnabled
);

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
  console.log('🔍 Opening client details for:', client.name);
  modalStore.openClientDetails(client);
  console.log('📝 Current screen after:', modalStore.currentScreen);
  console.log('📝 Selected client:', modalStore.selectedClient);
}

// === GESTIONNAIRES WEBSOCKET 100% ÉVÉNEMENTIELS ===

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
    console.log('✅ Client connected and added:', newClient.name);
  }
}

function handleClientDisconnected(event) {
  const clientId = event.data.client_id;
  const clientIndex = clients.value.findIndex(c => c.id === clientId);

  if (clientIndex !== -1) {
    const clientName = clients.value[clientIndex].name;
    clients.value.splice(clientIndex, 1);
    console.log('❌ Client disconnected and removed:', clientName);

    // Si le client déconnecté était celui en détails, revenir au main
    if (modalStore.selectedClient?.id === clientId && modalStore.currentScreen === 'client-details') {
      console.log('🔙 Client was in details view, going back to main');
      modalStore.goBack();
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
    console.log(`🔊 Client ${client.name} volume updated: ${volume}% (real volume)`);
  }
}

function handleClientNameChanged(event) {
  const { client_id, name } = event.data;
  const client = clients.value.find(c => c.id === client_id);

  if (client) {
    client.name = name;
    console.log(`📝 Client ${client_id} name updated: ${name}`);
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
    console.log(`🔇 Client ${client.name} mute updated: ${muted}`);
  }
}

function handleSystemStateChanged(event) {
  // OPTIM : Mise à jour du store + gestion multiroom activation
  unifiedStore.updateState(event);

  // Si le multiroom vient d'être activé, charger les clients initiaux + attendre événements
  if (event.data.multiroom_changed && unifiedStore.multiroomEnabled) {
    console.log('🏠 Multiroom activated - loading initial clients + waiting for real-time events');
    loadClients();
  }
  // Si le multiroom vient d'être désactivé, vider la liste immédiatement
  else if (event.data.multiroom_changed && !unifiedStore.multiroomEnabled) {
    console.log('🏠 Multiroom deactivated - clearing clients list');
    clients.value = [];

    // Revenir au main si on était dans les détails d'un client
    if (modalStore.currentScreen === 'client-details') {
      console.log('🔙 Multiroom deactivated, going back to main');
      modalStore.goToScreen('main');
    }
  }
}

// === LIFECYCLE OPTIM CORRIGÉ ===

onMounted(async () => {
  console.log('🚀 SnapcastControl mounted - OPTIM corrected mode');

  // S'abonner aux événements WebSocket temps réel AVANT de charger
  const subscriptions = [
    // Événements Snapcast clients (temps réel)
    on('snapcast', 'client_connected', handleClientConnected),
    on('snapcast', 'client_disconnected', handleClientDisconnected),
    on('snapcast', 'client_volume_changed', handleClientVolumeChanged),
    on('snapcast', 'client_name_changed', handleClientNameChanged),
    on('snapcast', 'client_mute_changed', handleClientMuteChanged),

    // Événements système (multiroom toggle)
    on('system', 'state_changed', handleSystemStateChanged)
  ];

  unsubscribeFunctions.push(...subscriptions);

  // OPTIM CORRIGÉ : Charger les clients initiaux SI multiroom actif
  if (isMultiroomActive.value) {
    await loadClients();
    console.log('📡 Initial clients loaded + subscribed to real-time events');
  } else {
    console.log('📡 Subscribed to events, waiting for multiroom activation');
  }
});

async function loadClients() {
  if (!isMultiroomActive.value) {
    clients.value = [];
    return;
  }

  try {
    const response = await axios.get('/api/routing/snapcast/clients');
    clients.value = response.data.clients || [];
    console.log(`📻 Loaded ${clients.value.length} initial clients`);
  } catch (error) {
    console.error('Error loading clients:', error);
    clients.value = [];
  }
}

onUnmounted(() => {
  console.log('🛑 SnapcastControl unmounted - cleaning up subscriptions');
  // Nettoyer tous les abonnements WebSocket
  unsubscribeFunctions.forEach(unsubscribe => unsubscribe());
});

// OPTIM CORRIGÉ : Garder le watcher pour sécurité + cleanup
import { watch } from 'vue';
watch(isMultiroomActive, async (newValue) => {
  if (newValue) {
    await loadClients();
  } else {
    clients.value = [];

    // Revenir au main si on était dans les détails
    if (modalStore.currentScreen === 'client-details') {
      modalStore.goToScreen('main');
    }
  }
});
</script>

<style scoped>


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

  0%,
  100% {
    opacity: 1;
  }

  50% {
    opacity: 0.5;
  }
}

.clients-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
</style>