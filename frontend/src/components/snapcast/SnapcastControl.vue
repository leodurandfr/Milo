<!-- frontend/src/components/snapcast/SnapcastControl.vue - Version sans modalStore -->
<template>
  <div v-if="!isMultiroomActive" class="not-active">
    <Icon name="multiroom" :size="148" color="var(--color-background-glass)" />
    <p class="text-mono">Le multiroom n’est pas activé</p>
  </div>

  <div v-else-if="clients.length === 0" class="not-active">
    <Icon name="multiroom" :size="148" color="var(--color-background-glass)" />
    <p class="text-mono">Aucun client n'est connecté</p>
    
  </div>

  <div v-else class="clients-list">
    <SnapclientItem v-for="client in clients" :key="client.id" :client="client" @volume-change="handleVolumeChange"
      @mute-toggle="handleMuteToggle" @show-details="handleShowDetails" />
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import useWebSocket from '@/services/websocket';
import axios from 'axios';
import SnapclientItem from './SnapclientItem.vue';
import Icon from '@/components/ui/Icon.vue';

const unifiedStore = useUnifiedAudioStore();
const { on } = useWebSocket();

// === ÉMISSIONS ===
const emit = defineEmits(['show-client-details']);

// État local
const clients = ref([]);

// Références pour nettoyage
let unsubscribeFunctions = [];

const isMultiroomActive = computed(() =>
  unifiedStore.multiroomEnabled
);

// === GESTIONNAIRES D'ÉVÉNEMENTS ===

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
  emit('show-client-details', client);
}

// === GESTIONNAIRES WEBSOCKET ===

function handleClientConnected(event) {
  const clientData = event.data.client;

  if (clientData && !clients.value.find(c => c.id === clientData.id)) {
    const newClient = {
      id: clientData.id,
      name: clientData.config?.name || clientData.host?.name || 'Unknown',
      volume: clientData.config?.volume?.percent || 0,
      muted: clientData.config?.volume?.muted || false,
      host: clientData.host?.name || 'Unknown',
      ip: clientData.host?.ip?.replace('::ffff:', '') || 'Unknown'
    };

    clients.value.push(newClient);
  }
}

function handleClientDisconnected(event) {
  const clientId = event.data.client_id;
  const clientIndex = clients.value.findIndex(c => c.id === clientId);

  if (clientIndex !== -1) {
    clients.value.splice(clientIndex, 1);
  }
}

function handleClientVolumeChanged(event) {
  const { client_id, volume, muted } = event.data;
  const client = clients.value.find(c => c.id === client_id);

  if (client) {
    client.volume = volume;
    if (muted !== undefined) {
      client.muted = muted;
    }
  }
}

function handleClientNameChanged(event) {
  const { client_id, name } = event.data;
  const client = clients.value.find(c => c.id === client_id);

  if (client) {
    client.name = name;
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
  }
}

function handleSystemStateChanged(event) {
  unifiedStore.updateState(event);
}

// === LIFECYCLE SIMPLIFIÉ ===

onMounted(async () => {
  // ✅ SOLUTION SIMPLE : Seulement WebSocket, pas d'appel API initial
  const subscriptions = [
    on('snapcast', 'client_connected', handleClientConnected),
    on('snapcast', 'client_disconnected', handleClientDisconnected),
    on('snapcast', 'client_volume_changed', handleClientVolumeChanged),
    on('snapcast', 'client_name_changed', handleClientNameChanged),
    on('snapcast', 'client_mute_changed', handleClientMuteChanged),
    on('system', 'state_changed', handleSystemStateChanged)
  ];

  unsubscribeFunctions.push(...subscriptions);
  
  // Les clients apparaîtront automatiquement via WebSocket
  // Pas besoin d'appel API initial !
});

onUnmounted(() => {
  // Nettoyer tous les abonnements WebSocket
  unsubscribeFunctions.forEach(unsubscribe => unsubscribe());
});

// Watcher simplifié
watch(isMultiroomActive, (newValue) => {
  if (!newValue) {
    clients.value = []; // Juste vider si désactivé
  }
  // Si activé, les clients arrivent via WebSocket automatiquement
});
</script>

<style scoped>
.not-active {
  display: flex;
  height: 100%;
  flex-direction: column;
  padding: var(--space-09) var(--space-05);
  border-radius: var(--radius-04);
  background: var(--color-background-neutral);
  gap: var(--space-04)
}

.not-active .text-mono {
  text-align: center;
  color: var(--color-text-secondary);
}



.clients-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}
</style>