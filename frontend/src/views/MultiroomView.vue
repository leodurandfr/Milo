<!-- frontend/src/views/MultiroomView.vue - Version avec WebSocket volume -->
<template>
  <div class="multiroom-view">
    <!-- Toggle Multiroom avec IconButton intégré -->
    <div class="toggle-wrapper">
      <div class="toggle-header">
        <h3>Multiroom</h3>
        <div class="controls-wrapper">
          <IconButton
            v-if="isMultiroomActive"
            icon="⚙️"
            @click="showSettings = true"
          />
          <Toggle
            v-model="isMultiroomActive"
            :disabled="unifiedStore.isTransitioning"
            @change="handleMultiroomToggle"
          />
        </div>
      </div>
    </div>

    <!-- Contenu principal -->
    <div class="main-content">
      <div v-if="!isMultiroomActive" class="multiroom-disabled">
        Multiroom non activé
      </div>

      <div v-else-if="clients.length === 0" class="no-clients">
        Aucun client connecté
      </div>

      <div v-else class="clients-list">
        <SnapclientItem
          v-for="client in clients"
          :key="client.id"
          :client="client"
          @volume-change="handleClientVolumeChange"
          @mute-toggle="handleClientMuteToggle"
          @show-details="handleShowClientDetails"
        />
      </div>
    </div>

    <!-- Navigation en bas -->
    <BottomNavigation />

    <!-- Modals -->
    <SnapcastSettings
      v-if="showSettings"
      @close="showSettings = false"
      @config-updated="fetchClients"
    />

    <SnapclientDetails
      v-if="selectedClient"
      :client="selectedClient"
      @close="selectedClient = null"
      @client-updated="fetchClients"
    />
  </div>
</template>

<script setup>
import { ref, computed, watchEffect, onUnmounted } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import useWebSocket from '@/services/websocket';
import axios from 'axios';
import IconButton from '@/components/ui/IconButton.vue';
import Toggle from '@/components/ui/Toggle.vue';
import SnapclientItem from '@/components/snapcast/SnapclientItem.vue';
import SnapcastSettings from '@/components/snapcast/SnapcastSettings.vue';
import SnapclientDetails from '@/components/snapcast/SnapclientDetails.vue';
import BottomNavigation from '@/components/navigation/BottomNavigation.vue';

const unifiedStore = useUnifiedAudioStore();
const { on } = useWebSocket();

// État local
const clients = ref([]);
const showSettings = ref(false);
const selectedClient = ref(null);

// État computed
const isMultiroomActive = computed(() => 
  unifiedStore.multiroomEnabled
);

// Gestion cleanup WebSocket
let unsubscribeWS = null;
let unsubscribeVolume = null;

// === LOGIQUE PRINCIPALE OPTIM ===

// watchEffect = onMounted + watcher combinés, plus simple et plus robuste
watchEffect(async () => {
  if (isMultiroomActive.value) {
    await fetchClients();
  } else {
    clients.value = [];
    selectedClient.value = null;
    showSettings.value = false;
  }
});

// === FONCTIONS ===

async function fetchClients() {
  try {
    const response = await axios.get('/api/routing/snapcast/clients');
    clients.value = response.data.clients || [];
  } catch (error) {
    console.error('Error fetching clients:', error);
    clients.value = [];
  }
}

// AJOUT : Fonction pour mettre à jour les volumes des clients depuis VolumeService
function updateClientsVolume(updatedClients) {
  console.log('Updating clients volume in MultiroomView:', updatedClients);
  
  for (const updatedClient of updatedClients) {
    const clientIndex = clients.value.findIndex(c => c.id === updatedClient.id);
    if (clientIndex !== -1) {
      clients.value[clientIndex].volume = updatedClient.new_volume;
      console.log(`Updated client ${updatedClient.name}: ${updatedClient.old_volume}% → ${updatedClient.new_volume}%`);
    }
  }
}

// === GESTION TOGGLE ===

async function handleMultiroomToggle(enabled) {
  await unifiedStore.setMultiroomEnabled(enabled);
}

// === GESTIONNAIRES D'ÉVÉNEMENTS ===

async function handleClientVolumeChange(clientId, volume) {
  // Mise à jour locale immédiate
  const client = clients.value.find(c => c.id === clientId);
  if (client) client.volume = volume;
  
  try {
    await axios.post(`/api/routing/snapcast/client/${clientId}/volume`, { volume });
  } catch (error) {
    console.error('Error updating volume:', error);
  }
}

async function handleClientMuteToggle(clientId, muted) {
  // Mise à jour locale immédiate
  const client = clients.value.find(c => c.id === clientId);
  if (client) client.muted = muted;
  
  try {
    await axios.post(`/api/routing/snapcast/client/${clientId}/mute`, { muted });
  } catch (error) {
    console.error('Error toggling mute:', error);
  }
}

function handleShowClientDetails(client) {
  selectedClient.value = client;
}

// === WEBSOCKET ===

// Setup WebSocket une seule fois
unsubscribeWS = on('system', 'state_changed', (event) => {
  // Synchroniser le store
  unifiedStore.updateState(event);
  
  // Rafraîchir si événement Snapcast ET multiroom actif
  if (event.source === 'snapcast' && event.data.snapcast_update && isMultiroomActive.value) {
    fetchClients();
  }
});

// AJOUT : Écouter les mises à jour de volume depuis VolumeService
unsubscribeVolume = on('snapcast', 'clients_volume_updated', (event) => {
  console.log('Received clients volume update in MultiroomView:', event.data.updated_clients);
  updateClientsVolume(event.data.updated_clients);
});

// === CLEANUP ===

onUnmounted(() => {
  if (unsubscribeWS) unsubscribeWS();
  if (unsubscribeVolume) unsubscribeVolume(); // AJOUT
});
</script>

<style scoped>
.multiroom-view {
  padding: 20px;
  max-width: 800px;
  margin: 0 auto;
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}

/* Toggle wrapper */
.toggle-wrapper {
  background: white;
  border: 1px solid #ddd;
  border-radius: 8px;
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

.multiroom-disabled, .no-clients {
  background: white;
  border: 1px solid #ddd;
  padding: 40px;
  text-align: center;
  color: #666;
}

/* Liste des clients - Directement sans wrapper */
.clients-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

/* Responsive */
@media (max-width: 600px) {
  .controls-wrapper {
    gap: 8px;
  }
}
</style>