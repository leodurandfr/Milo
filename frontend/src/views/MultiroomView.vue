<!-- frontend/src/views/MultiroomView.vue -->
<template>
  <div class="multiroom-view">
    <!-- En-tête avec contrôles globaux -->
    <div class="multiroom-header">
      <h1>Multiroom</h1>
      
      <div class="header-controls">
        <!-- Bouton Settings - SEULEMENT si multiroom actif -->
        <button 
          v-if="isMultiroomActive"
          @click="showSettings = true" 
          class="settings-btn"
        >
          Settings
        </button>
        
        <!-- Toggle Multiroom -->
        <div class="multiroom-toggle-inline">
          <label class="toggle">
            <input 
              type="checkbox" 
              :checked="unifiedStore.routingMode === 'multiroom'"
              @change="handleMultiroomToggle"
              :disabled="unifiedStore.isTransitioning"
            >
            <span class="slider"></span>
          </label>
          <span class="toggle-label">
            {{ unifiedStore.routingMode === 'multiroom' ? 'ON' : 'OFF' }}
          </span>
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

    <!-- Navigation en bas (composant réutilisable) -->
    <BottomNavigation />

    <!-- Popin Settings serveur -->
    <SnapcastSettings
      v-if="showSettings"
      @close="showSettings = false"
      @config-updated="handleConfigUpdated"
    />

    <!-- Popin détails client -->
    <SnapclientDetails
      v-if="selectedClient"
      :client="selectedClient"
      @close="selectedClient = null"
      @client-updated="handleClientUpdated"
    />
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import useWebSocket from '@/services/websocket';
import axios from 'axios';
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

// Empêcher le polling excessif
const lastRefreshTime = ref(0);
const REFRESH_COOLDOWN = 1000; // Minimum 1 seconde entre les refresh

// État computed
const isMultiroomActive = computed(() => 
  unifiedStore.routingMode === 'multiroom'
);

// Gestion des unsubscribe functions WebSocket
let unsubscribeFunctions = [];

// === FONCTIONS PRINCIPALES ===

async function fetchClients() {
  if (!isMultiroomActive.value) {
    clients.value = [];
    return;
  }
  
  try {
    const response = await axios.get('/api/routing/snapcast/monitoring');
    
    if (response.data.available) {
      clients.value = response.data.clients || [];
    } else {
      clients.value = [];
    }
  } catch (error) {
    console.error('Error fetching clients:', error);
    clients.value = [];
  }
}

// === GESTIONNAIRES D'ÉVÉNEMENTS ===

async function handleMultiroomToggle(event) {
  const newMode = event.target.checked ? 'multiroom' : 'direct';
  await unifiedStore.setRoutingMode(newMode);
}

// === GESTIONNAIRES SIMPLIFIÉS - MISE À JOUR LOCALE IMMÉDIATE ===

async function handleClientVolumeChange(clientId, volume) {
  // 1. Mise à jour immédiate du client local (comportement natif souhaité)
  const client = clients.value.find(c => c.id === clientId);
  if (client) {
    client.volume = volume;
  }
  
  // 2. Appel API - Snapcast se charge de notifier les autres clients
  try {
    await axios.post(`/api/routing/snapcast/client/${clientId}/volume`, { volume });
  } catch (error) {
    console.error('Error updating volume:', error);
    // En cas d'erreur, restaurer la valeur (optionnel)
  }
}

async function handleClientMuteToggle(clientId, muted) {
  // 1. Mise à jour immédiate du client local
  const client = clients.value.find(c => c.id === clientId);
  if (client) {
    client.muted = muted;
  }
  
  // 2. Appel API - Snapcast se charge du reste
  try {
    await axios.post(`/api/routing/snapcast/client/${clientId}/mute`, { muted });
  } catch (error) {
    console.error('Error toggling mute:', error);
  }
}

function handleConfigUpdated() {
  console.log('Server config updated, refreshing...');
  if (isMultiroomActive.value) {
    fetchClients();
  }
}

function handleShowClientDetails(client) {
  selectedClient.value = client;
}

function handleClientUpdated() {
  console.log('Client updated, refreshing...');
  if (isMultiroomActive.value) {
    fetchClients();
  }
}

// === SYNCHRONISATION MULTI-DEVICES ===

function handleRoutingUpdate(event) {
  // Synchronisation du toggle multiroom entre devices
  if (event.data.routing_changed) {
    console.log('Routing update received from another device, syncing...');
    // Le store sera automatiquement mis à jour via le WebSocket oakOS principal
    // Juste rafraîchir les clients si on passe en mode multiroom
    if (event.data.new_mode === 'multiroom' && isMultiroomActive.value) {
      fetchClients();
    }
  }
}

// === GESTION WEBSOCKET ===

function handleSnapcastUpdate(event) {
  // Éviter le polling excessif qui interfère avec l'interaction utilisateur
  if (event.data.snapcast_update && isMultiroomActive.value) {
    const now = Date.now();
    
    // Cooldown pour éviter les refresh trop fréquents
    if (now - lastRefreshTime.value > REFRESH_COOLDOWN) {
      console.log('Received Snapcast update via WebSocket, refreshing clients');
      lastRefreshTime.value = now;
      fetchClients();
    } else {
      console.log('Skipping refresh due to cooldown');
    }
  }
}

// === LIFECYCLE ===

onMounted(async () => {
  console.log('MultiroomView mounted');
  
  if (isMultiroomActive.value) {
    await fetchClients();
  }
  
  // Écouter les événements système pour synchronisation multi-devices
  const unsubscribe = on('system', 'state_changed', (event) => {
    // Synchroniser le store unifié (toggle multiroom, etc.)
    unifiedStore.updateState(event);
    
    // Synchronisation spécifique selon la source
    if (event.source === 'snapcast') {
      handleSnapcastUpdate(event);
    } else if (event.source === 'routing' && event.data.routing_changed) {
      handleRoutingUpdate(event);
    }
  });
  
  unsubscribeFunctions.push(unsubscribe);
  console.log('Multi-device sync activated for Snapcast and Routing');
});

onUnmounted(() => {
  // Nettoyer les abonnements WebSocket
  unsubscribeFunctions.forEach(unsubscribe => unsubscribe());
  console.log('Multi-device sync deactivated');
});

// Watcher pour le mode multiroom
watch(isMultiroomActive, async (newValue) => {
  if (newValue) {
    await fetchClients();
  } else {
    clients.value = [];
    selectedClient.value = null;
    showSettings.value = false;
  }
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

/* Toggle inline */
.multiroom-toggle-inline {
  display: flex;
  align-items: center;
  gap: 8px;
}

.toggle {
  position: relative;
  display: inline-block;
  width: 44px;
  height: 20px;
}

.toggle input {
  opacity: 0;
  width: 0;
  height: 0;
}

.slider {
  position: absolute;
  cursor: pointer;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background-color: #ccc;
  transition: 0.3s;
  border-radius: 20px;
}

.slider:before {
  position: absolute;
  content: "";
  height: 16px;
  width: 16px;
  left: 2px;
  bottom: 2px;
  background-color: white;
  transition: 0.3s;
  border-radius: 50%;
}

input:checked + .slider {
  background-color: #2196F3;
}

input:checked + .slider:before {
  transform: translateX(24px);
}

.toggle-label {
  font-size: 12px;
  font-weight: bold;
  min-width: 24px;
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