<!-- frontend/src/components/snapcast/SnapcastControl.vue - Version optimisée avec transitions fluides -->
<template>
  <div class="clients-container" :style="{ height: containerHeight }">
    <div class="clients-list" ref="clientsListRef" :class="{ 'with-background': showBackground }">
      <!-- MESSAGE : Multiroom désactivé -->
      <Transition name="message">
        <div v-if="showMessage" key="message" class="message-content">
          <Icon name="multiroom" :size="148" color="var(--color-background-glass)" />
          <p class="text-mono">{{ $t("Le multiroom n'est pas activé") }}</p>
        </div>
      </Transition>

      <!-- CLIENTS : Skeletons OU Items réels -->
      <Transition name="clients">
        <div v-if="!showMessage" key="clients" class="clients-wrapper">
          <SnapclientItem 
            v-for="client in displayClients" 
            :key="client.id" 
            :client="client"
            :is-loading="isLoadingClients"
            @volume-change="handleVolumeChange" 
            @mute-toggle="handleMuteToggle" 
            @show-details="handleShowDetails" 
          />
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

const emit = defineEmits(['show-client-details']);

const clientsListRef = ref(null);
const containerHeight = ref('232px');
const clients = ref([]);
const isLoadingClients = ref(false);

let resizeObserver = null;
let unsubscribeFunctions = [];

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
const isActivating = computed(() => unifiedStore.isMultiroomTransitioning);
const isDeactivating = computed(() => unifiedStore.isMultiroomDeactivating);

const showMessage = computed(() => {
  // Cacher le message pendant l'activation (pour montrer les skeletons)
  if (isActivating.value) return false;
  
  // Afficher le message si en cours de désactivation OU multiroom désactivé
  return isDeactivating.value || !isMultiroomActive.value;
});

const showBackground = computed(() => {
  return showMessage.value;
});

// Clients à afficher
const displayClients = computed(() => {
  // Si pas de clients et isLoading, afficher 3 placeholders vides
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
    // OPTION A : Afficher le cache immédiatement SANS skeletons
    clients.value = cache;
    isLoadingClients.value = false;
    
    // Fetch en arrière-plan pour mise à jour silencieuse
    const freshClients = await fetchSnapcastClients();
    
    // Mise à jour silencieuse (pas de transition)
    if (JSON.stringify(freshClients) !== JSON.stringify(cache)) {
      clients.value = sortClients(freshClients);
      saveCache(freshClients);
    }

  } else {
    // Pas de cache OU activation multiroom
    console.log('🚀 Chargement sans cache - Fetch API pour avoir les vrais IDs');
    
    // Fetch AVANT de créer les skeletons pour avoir les vrais IDs
    const freshClients = await fetchSnapcastClients();
    const sortedClients = sortClients(freshClients);
    
    console.log('✅ API retournée, création skeletons avec vrais IDs:', sortedClients.map(c => c.id));
    
    // Créer des skeletons AVEC LES VRAIS IDS des clients
    clients.value = sortedClients.map(client => ({
      id: client.id,  // ✅ Même ID = Vue réutilisera le composant
      name: '',
      volume: 0,
      muted: false
    }));
    isLoadingClients.value = true;
    
    console.log('🎭 Skeletons créés avec IDs:', clients.value.map(c => c.id));
    
    // Attendre 600ms pour voir les skeletons
    await new Promise(resolve => setTimeout(resolve, 600));
    
    console.log('⏰ 600ms écoulées, démarrage transition');
    
    // CRITIQUE : Changer isLoadingClients AVANT les données
    console.log('🎬 AVANT: isLoadingClients =', isLoadingClients.value);
    isLoadingClients.value = false;
    console.log('🎬 APRÈS: isLoadingClients =', isLoadingClients.value);
    
    console.log('⏳ Attente nextTick...');
    await nextTick();
    console.log('✅ nextTick terminé');
    
    // Puis mettre à jour les données (mêmes IDs = Vue met à jour au lieu de recréer)
    console.log('📝 AVANT: clients.value =', clients.value.map(c => `${c.id}:${c.name || 'empty'}`));
    clients.value = sortedClients;
    console.log('📝 APRÈS: clients.value =', clients.value.map(c => `${c.id}:${c.name}`));
    
    saveCache(sortedClients);
    console.log('💾 Cache sauvegardé');
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

  clients.value = sortClients([...clients.value, newClient]);
  saveCache(clients.value);
}

async function handleClientDisconnected(event) {
  if (isDeactivating.value) return;

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
  unifiedStore.setMultiroomTransitioning(true);
  
  // NE PAS créer de skeletons génériques ici
  // On va attendre le fetch API pour avoir les vrais IDs
  isLoadingClients.value = true;
  
  // Vider le cache car on veut des données fraîches
  clearCache();
}

async function handleMultiroomDisabling() {
  unifiedStore.setMultiroomDeactivating(true);
  clients.value = [];
  isLoadingClients.value = false;
  clearCache();
}

// === LIFECYCLE ===
onMounted(async () => {
  if (unifiedStore.isMultiroomTransitioning && isMultiroomActive.value) {
    unifiedStore.setMultiroomTransitioning(false);
  }

  if (unifiedStore.isMultiroomDeactivating && !isMultiroomActive.value) {
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
    unifiedStore.setMultiroomDeactivating(false);

    const forceNoCache = unifiedStore.isMultiroomTransitioning;
    await loadSnapcastClients(forceNoCache);

    unifiedStore.setMultiroomTransitioning(false);
  } else if (!newValue && oldValue) {
    unifiedStore.setMultiroomDeactivating(false);
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
  transition: background 400ms ease;
  position: relative;
}

.clients-list.with-background {
  background: var(--color-background-neutral);
}

.message-content {
  display: flex;
  min-height: 364px;
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

/* Transitions Message (plus rapides - 300ms) */
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

/* Transitions Clients (300ms pour activation rapide) */
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
</style>