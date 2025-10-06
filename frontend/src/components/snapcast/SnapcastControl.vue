<!-- frontend/src/components/snapcast/SnapcastControl.vue -->
<template>
  <div class="clients-container" :style="{ height: containerHeight }">
    <div class="clients-list" ref="clientsListRef" :class="{ 'with-background': showBackground }">
      <!-- MESSAGE : Multiroom désactivé -->
      <Transition name="message">
        <div v-if="showMessage" key="message" class="message-content">
          <Icon name="multiroom" :size="96" color="var(--color-background-glass)" />
          <p class="text-mono">{{ $t("Le multiroom est désactivé") }}</p>
        </div>
      </Transition>

      <!-- CLIENTS : Skeletons OU Items réels -->
      <Transition name="clients">
        <div v-if="!showMessage" key="clients" class="clients-wrapper">
          <SnapclientItem v-for="client in displayClients" :key="client.id" :client="client"
            :is-loading="isLoadingClients" @volume-change="handleVolumeChange" @mute-toggle="handleMuteToggle" />
        </div>
      </Transition>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import useWebSocket from '@/services/websocket';
import axios from 'axios';
import SnapclientItem from './SnapclientItem.vue';
import Icon from '@/components/ui/Icon.vue';

const unifiedStore = useUnifiedAudioStore();
const { on } = useWebSocket();

const clientsListRef = ref(null);
const containerHeight = ref('0px');
const clients = ref([]);
const isLoadingClients = ref(false);

// États locaux pour les transitions multiroom
const isMultiroomTransitioning = ref(false);
const isMultiroomDeactivating = ref(false);

// Mémorisation du dernier nombre de clients connus
const lastKnownClientCount = ref(3); // Défaut : 3 skeletons

// Constantes de hauteur
const ITEM_HEIGHT_DESKTOP = 72;
const ITEM_HEIGHT_MOBILE = 116;
const GAP_DESKTOP = 8;
const GAP_MOBILE = 8;

let resizeObserver = null;
let unsubscribeFunctions = [];

function calculateInitialHeight(clientsCount) {
  const isMobile = window.matchMedia('(max-aspect-ratio: 4/3)').matches;
  const itemHeight = isMobile ? ITEM_HEIGHT_MOBILE : ITEM_HEIGHT_DESKTOP;
  const gap = isMobile ? GAP_MOBILE : GAP_DESKTOP;

  return (clientsCount * itemHeight) + ((clientsCount - 1) * gap);
}

// === CACHE ===
const CACHE_KEY = 'snapcast_clients_cache';

function loadCache() {
  try {
    const cached = localStorage.getItem(CACHE_KEY);
    return cached ? JSON.parse(cached) : null;
  } catch {
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
  localStorage.removeItem(CACHE_KEY);
}

function sortClients(clientsList) {
  return [...clientsList].sort((a, b) => {
    if (a.host === "milo") return -1;
    if (b.host === "milo") return 1;
    return a.name.localeCompare(b.name);
  });
}

// === COMPUTED ===
const isMultiroomActive = computed(() => unifiedStore.multiroomEnabled);

const showMessage = computed(() => {
  if (isMultiroomTransitioning.value) return false;
  return isMultiroomDeactivating.value || !isMultiroomActive.value;
});

const showBackground = computed(() => {
  return showMessage.value;
});

const displayClients = computed(() => {
  if (clients.value.length === 0 && isLoadingClients.value) {
    return Array.from({ length: 3 }, (_, i) => ({
      id: `placeholder-${i}`,
      name: '',
      volume: 0,
      muted: false
    }));
  }
  return clients.value;
});

// === RESIZE OBSERVER ===
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

async function loadSnapcastClients(forceNoCache = false) {
  const cache = forceNoCache ? null : loadCache();

  if (cache && cache.length > 0 && !forceNoCache) {
    lastKnownClientCount.value = cache.length;
    clients.value = cache;
    isLoadingClients.value = false;
    containerHeight.value = `${calculateInitialHeight(lastKnownClientCount.value)}px`;

    const freshClients = await fetchSnapcastClients();

    if (JSON.stringify(freshClients) !== JSON.stringify(cache)) {
      clients.value = sortClients(freshClients);
      saveCache(freshClients);
    }

  } else {
    const freshClients = await fetchSnapcastClients();
    const sortedClients = sortClients(freshClients);

    // Mémoriser le nombre de clients (minimum 3 pour les skeletons par défaut)
    lastKnownClientCount.value = sortedClients.length || 3;
    containerHeight.value = `${calculateInitialHeight(lastKnownClientCount.value)}px`;

    clients.value = sortedClients.map(client => ({
      id: client.id,
      name: '',
      volume: 0,
      muted: false
    }));
    isLoadingClients.value = true;

    await new Promise(resolve => setTimeout(resolve, 600));

    isLoadingClients.value = false;
    await nextTick();

    clients.value = sortedClients;
    saveCache(sortedClients);
  }
}

// === HANDLERS ===
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

// === WEBSOCKET HANDLERS ===
async function handleClientConnected(event) {
  const { client_id, client_name, client_host, client_ip, volume, muted } = event.data;

  if (!client_id || isMultiroomDeactivating.value) return;
  if (clients.value.find(c => c.id === client_id)) return;

  const newClient = {
    id: client_id,
    name: client_name || client_host || 'Unknown',
    host: client_host || 'unknown',
    volume: volume || 0,
    muted: muted || false,
    ip: client_ip || 'Unknown'
  };

  clients.value = sortClients([...clients.value, newClient]);
  saveCache(clients.value);
}

async function handleClientDisconnected(event) {
  if (isMultiroomDeactivating.value) return;

  const clientId = event.data.client_id;
  clients.value = clients.value.filter(c => c.id !== clientId);
  saveCache(clients.value);
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

async function handleMultiroomEnabling() {
  isMultiroomTransitioning.value = true;
  isLoadingClients.value = true;
  clearCache();
}

async function handleMultiroomDisabling() {
  isMultiroomDeactivating.value = true;
  clients.value = [];
  isLoadingClients.value = false;
  clearCache();
}

// === LIFECYCLE ===
onMounted(async () => {
  if (isMultiroomTransitioning.value && isMultiroomActive.value) {
    isMultiroomTransitioning.value = false;
  }

  if (isMultiroomDeactivating.value && !isMultiroomActive.value) {
    isMultiroomDeactivating.value = false;
  }

  if (isMultiroomActive.value) {
    await loadSnapcastClients();
  } else {
    containerHeight.value = `${calculateInitialHeight(lastKnownClientCount.value)}px`;
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
    isMultiroomDeactivating.value = false;

    const forceNoCache = isMultiroomTransitioning.value;
    await loadSnapcastClients(forceNoCache);

    isMultiroomTransitioning.value = false;
  } else if (!newValue && oldValue) {
    isMultiroomDeactivating.value = false;
  }
});
</script>

<style scoped>
.clients-container {
  transition: height var(--transition-spring);
  overflow: visible;
  position: relative;
}

.clients-list {
  display: flex;
  flex-direction: column;
  overflow: visible;
  border-radius: var(--radius-04);
  transition: 400ms ease;
  position: relative;
}

.clients-list.with-background {
  background: var(--color-background-neutral);
}

.message-content {
  display: flex;
  min-height: 232px;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  gap: var(--space-04);
  padding: var(--space-05);
}

.message-content .text-mono {
  text-align: center;
  color: var(--color-text-secondary);
}

.clients-wrapper {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.message-enter-active {
  transition: opacity 300ms ease, transform 300ms ease;
}

.message-leave-active {
  transition: opacity 300ms ease, transform 300ms ease;
  position: absolute;
  width: 100%;
}

.message-enter-from {
  opacity: 0;
  transform: translateY(12px);
}

.message-leave-to {
  opacity: 0;
  transform: translateY(-12px);
}

.clients-enter-active {
  transition: opacity 300ms ease 100ms, transform 300ms ease 100ms;
}

.clients-leave-active {
  transition: opacity 300ms ease, transform 300ms ease;
  position: absolute;
  width: 100%;
}

.clients-enter-from {
  opacity: 0;
  transform: translateY(12px);
}

.clients-leave-to {
  opacity: 0;
  transform: translateY(-12px);
}

@media (max-aspect-ratio: 4/3) {
  .message-content {
    min-height: 364px;
  }
}
</style>