<!-- frontend/src/views/MultiroomView.vue - Version refactorisée -->
<template>
  <div class="multiroom-view">
    <!-- En-tête avec contrôles globaux -->
    <div class="multiroom-header">
      <h1>Multiroom</h1>
      
      <div class="header-controls">
        <button 
          v-if="isMultiroomActive"
          @click="showSettings = true" 
          class="settings-btn"
        >
          Settings
        </button>
      </div>
    </div>

    <!-- Toggle Multiroom -->
    <MultiroomToggle />

    <!-- Contenu principal -->
    <div class="main-content">
      <div v-if="!isMultiroomActive" class="multiroom-disabled">
        Multiroom non activé
      </div>

      <div v-else-if="clients.length === 0" class="no-clients">
        Aucun client connecté
      </div>

      <div v-else class="clients-section">
        <h2>Clients Snapcast</h2>
        
        <div class="clients-list">
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
import MultiroomToggle from '@/components/routing/MultiroomToggle.vue';
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

// État computed - Refactorisé
const isMultiroomActive = computed(() => 
  unifiedStore.multiroomEnabled  // Refactorisé
);

// Gestion cleanup WebSocket
let unsubscribeWS = null;

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

// === CLEANUP ===

onUnmounted(() => {
  if (unsubscribeWS) unsubscribeWS();
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

/* En-tête */
.multiroom-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: white;
  border: 1px solid #ddd;
  padding: 20px;
  margin-bottom: 20px;
}

.multiroom-header h1 {
  margin: 0;
  font-size: 24px;
}

.header-controls {
  display: flex;
  align-items: center;
  gap: 20px;
}

/* Boutons */
.settings-btn {
  padding: 8px 16px;
  background: #f5f5f5;
  border: 1px solid #ddd;
  cursor: pointer;
  font-size: 14px;
}

.settings-btn:hover {
  background: #e9e9e9;
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

/* Section clients */
.clients-section {
  background: white;
  border: 1px solid #ddd;
  padding: 20px;
}

.clients-section h2 {
  margin: 0 0 16px 0;
  font-size: 18px;
}

.clients-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
</style>