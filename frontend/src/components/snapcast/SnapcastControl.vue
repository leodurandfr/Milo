<!-- frontend/src/components/snapcast/SnapcastControl.vue - Version sans modalStore -->
<template>
  <div v-if="!isMultiroomActive" class="not-active">
    <Icon name="multiroom" :size="148" color="var(--color-background-glass)" />
    <p class="text-mono">Le multiroom n’est pas activé</p>
  </div>

  <div v-else-if="clients.length === 0" class="not-active">
    <span class="loading"></span>
    Aucun client n'est connecté
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
  console.log('🔍 SnapcastControl: Emitting show-client-details for:', client.name);
  // CHANGEMENT : Émission vers le parent SnapcastModal au lieu d'utiliser modalStore
  emit('show-client-details', client);
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
  }
}

// === LIFECYCLE OPTIM ===

onMounted(async () => {
  console.log('🚀 SnapcastControl mounted - OPTIM mode');

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

  // OPTIM : Charger les clients initiaux SI multiroom actif
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

// Watcher pour sécurité + cleanup
watch(isMultiroomActive, async (newValue) => {
  if (newValue) {
    await loadClients();
  } else {
    clients.value = [];
  }
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