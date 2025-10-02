<!-- frontend/src/components/snapcast/SnapcastControl.vue -->
<template>
  <div v-if="!isMultiroomActive" class="not-active">
    <Icon name="multiroom" :size="148" color="var(--color-background-glass)" />
    <p class="text-mono">{{ $t("Le multiroom n'est pas activé") }}</p>
  </div>

  <div v-else class="clients-container">
    <!-- Skeletons pendant premier chargement -->
    <div v-if="showSkeletons" class="clients-list">
      <SnapclientItemSkeleton v-for="i in 3" :key="`skeleton-${i}`" />
    </div>

    <!-- Message si aucun client -->
    <div v-else-if="clients.length === 0" class="not-active">
      <Icon name="multiroom" :size="148" color="var(--color-background-glass)" />
      <p class="text-mono">{{ $t("Aucun client n'est connecté") }}</p>
    </div>

    <!-- Liste des clients -->
    <div v-else class="clients-list">
      <SnapclientItem v-for="client in clients" :key="client.id" :client="client"
        :class="{ 'fade-in': fadingInClients.includes(client.id), 'fade-out': fadingOutClients.includes(client.id) }"
        @volume-change="handleVolumeChange" @mute-toggle="handleMuteToggle" @show-details="handleShowDetails" />
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import useWebSocket from '@/services/websocket';
import axios from 'axios';
import SnapclientItem from './SnapclientItem.vue';
import SnapclientItemSkeleton from './SnapclientItemSkeleton.vue';
import Icon from '@/components/ui/Icon.vue';

const unifiedStore = useUnifiedAudioStore();
const { on } = useWebSocket();

const emit = defineEmits(['show-client-details']);

// === CACHE MANAGEMENT ===
const CACHE_KEY = 'snapcast_clients_cache';

function loadCache() {
  try {
    const cached = localStorage.getItem(CACHE_KEY);
    if (!cached) return null;
    const parsed = JSON.parse(cached);
    return parsed.length > 0 ? parsed : null;
  } catch (error) {
    console.error('Error loading cache:', error);
    return null;
  }
}

function saveCache(clientsList) {
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify(clientsList));
  } catch (error) {
    console.error('Error saving cache:', error);
  }
}

// === CLIENT SORTING ===
function sortClients(clientsList) {
  return [...clientsList].sort((a, b) => {
    if (a.host === "milo") return -1;
    if (b.host === "milo") return 1;
    return a.name.localeCompare(b.name);
  });
}

// État local - Initialiser avec le cache immédiatement
const cache = loadCache();
const clients = ref(cache ? sortClients(cache) : []);
const showSkeletons = ref(!cache);
const fadingInClients = ref([]);
const fadingOutClients = ref([]);
let unsubscribeFunctions = [];

const isMultiroomActive = computed(() => unifiedStore.multiroomEnabled);

// === ANIMATIONS ===
async function fadeOutClients(clientIds) {
  if (clientIds.length === 0) return;

  fadingOutClients.value = clientIds;
  await new Promise(resolve => setTimeout(resolve, 200));

  clients.value = clients.value.filter(c => !clientIds.includes(c.id));
  fadingOutClients.value = [];
}

async function fadeInClients(newClients) {
  if (newClients.length === 0) return;

  const newIds = newClients.map(c => c.id);
  fadingInClients.value = newIds;

  clients.value = sortClients([...clients.value, ...newClients]);

  await new Promise(resolve => setTimeout(resolve, 200));
  fadingInClients.value = [];
}

// === FETCH CLIENTS ===
async function fetchSnapcastClients() {
  try {
    const response = await axios.get('/api/routing/snapcast/clients');
    if (response.data.clients) {
      return sortClients(response.data.clients);
    }
    return [];
  } catch (error) {
    console.error('Error fetching clients:', error);
    return [];
  }
}

// === LOAD CLIENTS ===
async function loadSnapcastClients() {
  const cache = loadCache();

  if (!cache || cache.length === 0) {
    // PREMIÈRE FOIS - Skeletons
    const freshClients = await fetchSnapcastClients();

    showSkeletons.value = false;
    clients.value = freshClients;

    if (freshClients.length > 0) {
      saveCache(freshClients);
    }
  } else {
    // FOIS SUIVANTES - Cache déjà affiché
    const freshClients = await fetchSnapcastClients();

    const cacheIds = new Set(cache.map(c => c.id));
    const freshIds = new Set(freshClients.map(c => c.id));

    const removedIds = [...cacheIds].filter(id => !freshIds.has(id));
    const addedClients = freshClients.filter(c => !cacheIds.has(c.id));

    if (removedIds.length > 0) {
      await fadeOutClients(removedIds);
    }

    if (addedClients.length > 0) {
      await fadeInClients(addedClients);
    }

    clients.value = sortClients(freshClients);
    saveCache(freshClients);
  }
}

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

// === WEBSOCKET HANDLERS ===
async function handleClientConnected(event) {
  console.log('🟢 handleClientConnected CALLED', event);
  console.log('🟢 event.data:', event.data);
  
  // CORRECTION : Extraire AUSSI volume et muted
  const { client_id, client_name, client_host, client_ip, volume, muted } = event.data;
  
  if (client_id && !clients.value.find(c => c.id === client_id)) {
    const newClient = {
      id: client_id,
      name: client_name || client_host || 'Unknown',
      host: client_host || 'unknown',
      volume: volume || 0,     // CORRECTION : Utiliser la valeur de l'event
      muted: muted || false,   // CORRECTION : Utiliser la valeur de l'event
      ip: client_ip || 'Unknown'
    };
    
    console.log('🟢 New client object:', newClient);
    await fadeInClients([newClient]);
    saveCache(clients.value);
  }
}

async function handleClientDisconnected(event) {
  const clientId = event.data.client_id;
  if (clients.value.find(c => c.id === clientId)) {
    await fadeOutClients([clientId]);
    saveCache(clients.value);
  }
}

function handleClientVolumeChanged(event) {
  const { client_id, volume, muted } = event.data;
  const client = clients.value.find(c => c.id === client_id);
  if (client) {
    client.volume = volume;
    if (muted !== undefined) client.muted = muted;
  }
}

function handleClientNameChanged(event) {
  const { client_id, name } = event.data;
  const client = clients.value.find(c => c.id === client_id);
  if (client) {
    client.name = name;
    clients.value = sortClients(clients.value);
  }
}

function handleClientMuteChanged(event) {
  const { client_id, muted, volume } = event.data;
  const client = clients.value.find(c => c.id === client_id);
  if (client) {
    client.muted = muted;
    if (volume !== undefined) client.volume = volume;
  }
}

function handleSystemStateChanged(event) {
  unifiedStore.updateState(event);
}

// === LIFECYCLE ===
onMounted(async () => {
  if (isMultiroomActive.value) {
    await loadSnapcastClients();
  }

  console.log('🔵 Registering WebSocket handlers');

  unsubscribeFunctions.push(
    on('snapcast', 'client_connected', (event) => {
      console.log('🔵 RAW client_connected event:', event);
      handleClientConnected(event);
    }),
    on('snapcast', 'client_disconnected', handleClientDisconnected),
    on('snapcast', 'client_volume_changed', handleClientVolumeChanged),
    on('snapcast', 'client_name_changed', handleClientNameChanged),
    on('snapcast', 'client_mute_changed', handleClientMuteChanged),
    on('system', 'state_changed', handleSystemStateChanged)
  );
});

onUnmounted(() => {
  unsubscribeFunctions.forEach(unsubscribe => unsubscribe());
});

// Watcher pour le mode multiroom
watch(isMultiroomActive, async (newValue) => {
  if (newValue) {
    await loadSnapcastClients();
  } else {
    clients.value = [];
    showSkeletons.value = false;
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
  gap: var(--space-04);
}

.not-active .text-mono {
  text-align: center;
  color: var(--color-text-secondary);
}

.clients-container {
  height: 100%;
}

.clients-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.fade-in {
  animation: fadeIn 200ms ease-out;
}

.fade-out {
  animation: fadeOut 200ms ease-out;
  pointer-events: none;
}

@keyframes fadeIn {
  from {
    opacity: 0;
    transform: translateY(-8px);
  }

  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@keyframes fadeOut {
  from {
    opacity: 1;
    transform: translateY(0);
  }

  to {
    opacity: 0;
    transform: translateY(-8px);
  }
}
</style>