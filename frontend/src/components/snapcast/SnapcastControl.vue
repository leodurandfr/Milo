<!-- frontend/src/components/snapcast/SnapcastControl.vue -->
<template>
  <div class="clients-container" 
       :class="{ 'show-background': showBackground }" 
       :style="{ height: containerHeight }">
    
    <div class="clients-list" ref="clientsListRef">
      <!-- MESSAGE : Multiroom désactivé OU en cours de désactivation -->
      <div v-if="showMessage" class="message-content">
        <Icon name="multiroom" :size="148" color="var(--color-background-glass)" />
        <p class="text-mono">{{ $t("Le multiroom n'est pas activé") }}</p>
      </div>
      
      <!-- SKELETONS : Activation en cours -->
      <template v-else-if="showSkeletons">
        <SnapclientItemSkeleton v-for="i in 3" :key="`skeleton-${i}`" />
      </template>
      
      <!-- CLIENTS : Liste normale -->
      <template v-else>
        <SnapclientItem 
          v-for="client in clients" 
          :key="client.id" 
          :client="client"
          :class="{ 
            'fade-in': fadingInClients.includes(client.id), 
            'fade-out': fadingOutClients.includes(client.id) 
          }"
          @volume-change="handleVolumeChange" 
          @mute-toggle="handleMuteToggle" 
          @show-details="handleShowDetails" 
        />
      </template>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import useWebSocket from '@/services/websocket';
import axios from 'axios';
import SnapclientItem from './SnapclientItem.vue';
import SnapclientItemSkeleton from './SnapclientItemSkeleton.vue';
import Icon from '@/components/ui/Icon.vue';

const unifiedStore = useUnifiedAudioStore();
const { on } = useWebSocket();

const emit = defineEmits(['show-client-details']);

const clientsListRef = ref(null);
const containerHeight = ref('232px');

let resizeObserver = null;

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

function clearCache() {
  try {
    localStorage.removeItem(CACHE_KEY);
    console.log('🗑️ Cache cleared');
  } catch (error) {
    console.error('Error clearing cache:', error);
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

// États
const clients = ref([]);
const showSkeletons = ref(false);
const fadingInClients = ref([]);
const fadingOutClients = ref([]);
let unsubscribeFunctions = [];

const isMultiroomActive = computed(() => unifiedStore.multiroomEnabled);
const isDeactivating = computed(() => unifiedStore.isMultiroomDeactivating);

// === COMPUTED POUR L'AFFICHAGE ===
const showMessage = computed(() => {
  if (!isMultiroomActive.value && !unifiedStore.isMultiroomTransitioning) {
    return true;
  }
  if (isDeactivating.value && clients.value.length === 0) {
    return true;
  }
  return false;
});

const showBackground = computed(() => {
  return showMessage.value;
});

// === RESIZE OBSERVER SETUP ===
function setupResizeObserver() {
  if (resizeObserver) {
    resizeObserver.disconnect();
  }

  resizeObserver = new ResizeObserver(entries => {
    if (entries[0]) {
      const newHeight = entries[0].contentRect.height;
      const currentHeight = parseFloat(containerHeight.value);

      if (Math.abs(newHeight - currentHeight) > 2) {
        containerHeight.value = `${newHeight}px`;
      }
    }
  });

  if (clientsListRef.value) {
    resizeObserver.observe(clientsListRef.value);
  }
}

// === ANIMATIONS ===
async function fadeOutAllClients() {
  if (clients.value.length === 0) return;

  const allIds = clients.value.map(c => c.id);
  fadingOutClients.value = allIds;

  await new Promise(resolve => setTimeout(resolve, 200));

  clients.value = [];
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
    return response.data.clients ? sortClients(response.data.clients) : [];
  } catch (error) {
    console.error('Error fetching clients:', error);
    return [];
  }
}

// === LOAD CLIENTS ===
async function loadSnapcastClients(forceNoCache = false) {
  const cache = forceNoCache ? null : loadCache();

  if (cache && cache.length > 0) {
    console.log('📦 Using cache for instant display');
    clients.value = cache;
    showSkeletons.value = false;

    const freshClients = await fetchSnapcastClients();

    const cacheIds = new Set(cache.map(c => c.id));
    const freshIds = new Set(freshClients.map(c => c.id));

    const removedIds = [...cacheIds].filter(id => !freshIds.has(id));
    const addedClients = freshClients.filter(c => !cacheIds.has(c.id));

    if (removedIds.length > 0) {
      fadingOutClients.value = removedIds;
      await new Promise(resolve => setTimeout(resolve, 200));
      clients.value = clients.value.filter(c => !removedIds.includes(c.id));
      fadingOutClients.value = [];
    }

    if (addedClients.length > 0) {
      await fadeInClients(addedClients);
    }

    clients.value = sortClients(freshClients);
    saveCache(freshClients);

  } else {
    console.log('⏳ No cache, showing skeletons then fetching');
    showSkeletons.value = true;

    const freshClients = await fetchSnapcastClients();

    await new Promise(resolve => setTimeout(resolve, 300));

    showSkeletons.value = false;

    if (freshClients.length > 0) {
      await fadeInClients(freshClients);
      saveCache(freshClients);
    }
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
  const { client_id, client_name, client_host, client_ip, volume, muted } = event.data;

  if (!client_id || isDeactivating.value) return;
  if (clients.value.find(c => c.id === client_id)) return;

  const newClient = {
    id: client_id,
    name: client_name || client_host || 'Unknown',
    host: client_host || 'unknown',
    volume: volume || 0,
    muted: muted || false,
    ip: client_ip || 'Unknown'
  };

  await fadeInClients([newClient]);
  saveCache(clients.value);
}

async function handleClientDisconnected(event) {
  if (isDeactivating.value) return;

  const clientId = event.data.client_id;
  const client = clients.value.find(c => c.id === clientId);

  if (client) {
    fadingOutClients.value = [clientId];
    await new Promise(resolve => setTimeout(resolve, 200));
    clients.value = clients.value.filter(c => c.id !== clientId);
    fadingOutClients.value = [];
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

async function handleMultiroomEnabling(event) {
  console.log('🚀 MULTIROOM ENABLING EVENT REÇU');

  unifiedStore.setMultiroomTransitioning(true);
  clients.value = [];
  showSkeletons.value = true;
  clearCache();

  console.log('✅ Skeletons displayed, cache cleared, waiting for services to start');
}

async function handleMultiroomDisabling(event) {
  console.log('🚨 MULTIROOM DISABLING EVENT REÇU');

  unifiedStore.setMultiroomDeactivating(true);
  await fadeOutAllClients();
  showSkeletons.value = false;
  clearCache();

  console.log('✅ Waiting for backend confirmation to reset isDeactivating flag');
}

// === LIFECYCLE ===
onMounted(async () => {
  const currentMultiroomState = isMultiroomActive.value;

  if (unifiedStore.isMultiroomTransitioning && currentMultiroomState) {
    console.log('⚠️ Reset isMultiroomTransitioning (multiroom already active)');
    unifiedStore.setMultiroomTransitioning(false);
  }

  if (unifiedStore.isMultiroomDeactivating && !currentMultiroomState) {
    console.log('⚠️ Reset isMultiroomDeactivating (multiroom already inactive)');
    unifiedStore.setMultiroomDeactivating(false);
  }

  if (isMultiroomActive.value) {
    await loadSnapcastClients();
  }

  await nextTick();
  setupResizeObserver();

  unsubscribeFunctions.push(
    on('snapcast', 'client_connected', handleClientConnected),
    on('snapcast', 'client_disconnected', handleClientDisconnected),
    on('snapcast', 'client_volume_changed', handleClientVolumeChanged),
    on('snapcast', 'client_name_changed', handleClientNameChanged),
    on('snapcast', 'client_mute_changed', handleClientMuteChanged),
    on('system', 'state_changed', handleSystemStateChanged),
    on('routing', 'multiroom_enabling', handleMultiroomEnabling),
    on('routing', 'multiroom_disabling', handleMultiroomDisabling)
  );
});

onUnmounted(() => {
  unsubscribeFunctions.forEach(unsubscribe => unsubscribe());
  if (resizeObserver) {
    resizeObserver.disconnect();
  }
});

// === WATCHERS ===
watch(isMultiroomActive, async (newValue, oldValue) => {
  if (newValue && !oldValue) {
    console.log('✅ Multiroom services started, loading clients');
    unifiedStore.setMultiroomDeactivating(false);

    const forceNoCache = unifiedStore.isMultiroomTransitioning;
    await loadSnapcastClients(forceNoCache);

    unifiedStore.setMultiroomTransitioning(false);
  } else if (!newValue && oldValue) {
    console.log('✅ Multiroom disabled confirmed, resetting isDeactivating flag');
    unifiedStore.setMultiroomDeactivating(false);
  }
});
</script>

<style scoped>
.clients-container {
  transition: height var(--transition-spring);
  overflow: visible;
}

.clients-container.show-background .clients-list {
  background: var(--color-background-neutral);
  border-radius: var(--radius-04);
}

.clients-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
  overflow: visible;
}

.message-content {
  display: flex;
  /* height: 232px; */
  flex-direction: column;
  justify-content: center;
  align-items: center;
  gap: var(--space-04);
  padding: var(--space-05);
  
  opacity: 0;
  animation: fadeIn 400ms ease-out forwards;
}

.message-content .text-mono {
  text-align: center;
  color: var(--color-text-secondary);
}

@keyframes fadeIn {
  from {
    opacity: 0;
  }
  to {
    opacity: 1;
  }
}

.fade-in {
  animation: fadeIn 200ms ease-out;
}

.fade-out {
  animation: fadeOut 200ms ease-out;
  pointer-events: none;
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