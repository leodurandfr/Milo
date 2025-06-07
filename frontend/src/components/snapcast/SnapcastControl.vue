<!-- frontend/src/components/snapcast/SnapcastControl.vue -->
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
      <div v-for="client in clients" :key="client.id" class="client-item">
        <div class="client-info">
          <div class="client-name">{{ client.name }}</div>
          <div class="client-details">{{ client.host }} • {{ client.ip }}</div>
        </div>
        
        <div class="client-controls">
          <button 
            @click="toggleMute(client)"
            :class="['mute-btn', { muted: client.muted }]"
            :disabled="updatingClients.has(client.id)"
          >
            {{ client.muted ? '🔇' : '🔊' }}
          </button>
          
          <div class="volume-control">
            <input
              type="range"
              min="0"
              max="100"
              :value="getVolume(client)"
              @input="handleVolumeInput(client, $event.target.value)"
              @change="handleVolumeChange(client, $event.target.value)"
              :disabled="client.muted || updatingClients.has(client.id)"
              class="volume-slider"
            >
            <span class="volume-label">{{ getVolume(client) }}%</span>
          </div>
        </div>
      </div>
    </div>
    
    <div class="actions">
      <button @click="refresh" :disabled="loading" class="refresh-btn">
        {{ loading ? 'Actualisation...' : 'Actualiser' }}
      </button>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import axios from 'axios';

const unifiedStore = useUnifiedAudioStore();
const clients = ref([]);
const loading = ref(false);
const updatingClients = ref(new Set());
const volumeTimeouts = ref(new Map());
const localVolumes = ref(new Map());

const isMultiroomActive = computed(() => 
  unifiedStore.routingMode === 'multiroom'
);

function getVolume(client) {
  // Utiliser le volume local temporaire si disponible
  return localVolumes.value.get(client.id) ?? client.volume;
}

async function fetchClients() {
  if (!isMultiroomActive.value) {
    clients.value = [];
    return;
  }
  
  loading.value = true;
  try {
    const response = await axios.get('/api/routing/snapcast/clients');
    clients.value = response.data.clients || [];
    
    // Nettoyer les volumes locaux pour les clients qui n'existent plus
    const currentClientIds = new Set(clients.value.map(c => c.id));
    for (const [clientId] of localVolumes.value) {
      if (!currentClientIds.has(clientId)) {
        localVolumes.value.delete(clientId);
      }
    }
  } catch (error) {
    console.error('Error fetching clients:', error);
    clients.value = [];
  } finally {
    loading.value = false;
  }
}

function handleVolumeInput(client, volume) {
  // Feedback visuel immédiat
  const newVolume = parseInt(volume);
  localVolumes.value.set(client.id, newVolume);
}

function handleVolumeChange(client, volume) {
  const newVolume = parseInt(volume);
  
  // Debouncing - annuler le timeout précédent
  if (volumeTimeouts.value.has(client.id)) {
    clearTimeout(volumeTimeouts.value.get(client.id));
  }
  
  // Programmer la mise à jour avec délai
  const timeout = setTimeout(() => {
    updateVolume(client, newVolume);
    volumeTimeouts.value.delete(client.id);
  }, 300);
  
  volumeTimeouts.value.set(client.id, timeout);
}

async function updateVolume(client, volume) {
  updatingClients.value.add(client.id);
  
  try {
    const response = await axios.post(`/api/routing/snapcast/client/${client.id}/volume`, { 
      volume 
    });
    
    if (response.data.status === 'success') {
      // Mettre à jour le client avec la nouvelle valeur
      client.volume = volume;
      localVolumes.value.delete(client.id);
    } else {
      // Restaurer en cas d'erreur
      localVolumes.value.delete(client.id);
    }
  } catch (error) {
    console.error('Error updating volume:', error);
    localVolumes.value.delete(client.id);
  } finally {
    updatingClients.value.delete(client.id);
  }
}

async function toggleMute(client) {
  if (updatingClients.value.has(client.id)) return;
  
  updatingClients.value.add(client.id);
  const newMuted = !client.muted;
  const originalMuted = client.muted;
  
  // Feedback immédiat
  client.muted = newMuted;
  
  try {
    const response = await axios.post(`/api/routing/snapcast/client/${client.id}/mute`, { 
      muted: newMuted 
    });
    
    if (response.data.status !== 'success') {
      // Restaurer en cas d'erreur
      client.muted = originalMuted;
    }
  } catch (error) {
    console.error('Error toggling mute:', error);
    // Restaurer en cas d'erreur
    client.muted = originalMuted;
  } finally {
    updatingClients.value.delete(client.id);
  }
}

async function refresh() {
  await fetchClients();
}

// Rafraîchissement automatique quand le mode multiroom change
let refreshInterval = null;

function startAutoRefresh() {
  if (refreshInterval) return;
  
  refreshInterval = setInterval(() => {
    if (isMultiroomActive.value && !loading.value) {
      fetchClients();
    }
  }, 10000); // Rafraîchir toutes les 10 secondes
}

function stopAutoRefresh() {
  if (refreshInterval) {
    clearInterval(refreshInterval);
    refreshInterval = null;
  }
}

onMounted(async () => {
  if (isMultiroomActive.value) {
    await fetchClients();
    startAutoRefresh();
  }
});

onUnmounted(() => {
  stopAutoRefresh();
  
  // Nettoyer les timeouts en cours
  for (const timeout of volumeTimeouts.value.values()) {
    clearTimeout(timeout);
  }
});

// Watcher pour le mode multiroom
import { watch } from 'vue';
watch(isMultiroomActive, async (newValue) => {
  if (newValue) {
    await fetchClients();
    startAutoRefresh();
  } else {
    clients.value = [];
    stopAutoRefresh();
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

.client-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px;
  border: 1px solid #e0e0e0;
  border-radius: 6px;
  background: #fafafa;
}

.client-info {
  flex: 1;
}

.client-name {
  font-weight: bold;
  color: #333;
  font-size: 14px;
}

.client-details {
  font-size: 12px;
  color: #666;
  margin-top: 2px;
}

.client-controls {
  display: flex;
  align-items: center;
  gap: 12px;
}

.mute-btn {
  width: 36px;
  height: 36px;
  border: 1px solid #ddd;
  border-radius: 4px;
  background: white;
  cursor: pointer;
  font-size: 16px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s;
}

.mute-btn:hover:not(:disabled) {
  background: #f0f0f0;
}

.mute-btn.muted {
  background: #dc3545;
  color: white;
  border-color: #dc3545;
}

.mute-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.volume-control {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 120px;
}

.volume-slider {
  flex: 1;
  height: 4px;
  background: #ddd;
  outline: none;
  border-radius: 2px;
  appearance: none;
}

.volume-slider::-webkit-slider-thumb {
  appearance: none;
  width: 16px;
  height: 16px;
  background: #2196F3;
  border-radius: 50%;
  cursor: pointer;
}

.volume-slider::-moz-range-thumb {
  width: 16px;
  height: 16px;
  background: #2196F3;
  border-radius: 50%;
  cursor: pointer;
  border: none;
}

.volume-slider:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.volume-label {
  font-size: 12px;
  color: #666;
  width: 32px;
  text-align: right;
}

.actions {
  margin-top: 16px;
  text-align: center;
}

.refresh-btn {
  padding: 8px 16px;
  border: 1px solid #ddd;
  border-radius: 4px;
  background: white;
  cursor: pointer;
  font-size: 14px;
  transition: all 0.2s;
}

.refresh-btn:hover:not(:disabled) {
  background: #f0f0f0;
}

.refresh-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>