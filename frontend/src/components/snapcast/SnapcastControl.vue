<!-- frontend/src/components/snapcast/SnapcastControl.vue - VERSION FINALE avec événements symétriques -->
<template>
  <div v-if="(!isMultiroomActive && !unifiedStore.isMultiroomTransitioning) || (isDeactivating && clients.length === 0)" class="not-active">
    <Icon name="multiroom" :size="148" color="var(--color-background-glass)" />
    <p class="text-mono">{{ $t("Le multiroom n'est pas activé") }}</p>
  </div>

  <div v-else class="clients-container">
    <!-- Skeletons pendant chargement/activation -->
    <div v-if="showSkeletons" class="clients-list">
      <SnapclientItemSkeleton v-for="i in 3" :key="`skeleton-${i}`" class="fade-in" />
    </div>

    <!-- Message si aucun client (sauf pendant désactivation) -->
    <div v-else-if="clients.length === 0 && !isDeactivating" class="not-active">
      <Icon name="multiroom" :size="148" color="var(--color-background-glass)" />
      <p class="text-mono">{{ $t("Aucun client n'est connecté") }}</p>
    </div>

    <!-- Liste des clients -->
    <div v-else class="clients-list">
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
    // AVEC CACHE : Affichage immédiat
    console.log('📦 Using cache for instant display');
    clients.value = cache;
    showSkeletons.value = false;
    
    // Fetch en arrière-plan
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
    // SANS CACHE : Skeletons puis fade-in groupé
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

// === HANDLER POUR ACTIVATION ANTICIPÉE ===
async function handleMultiroomEnabling(event) {
  console.log('🚀 MULTIROOM ENABLING EVENT REÇU');
  
  // Activer le flag de transition dans le store
  unifiedStore.setMultiroomTransitioning(true);
  
  // Vider immédiatement les clients et afficher skeletons
  clients.value = [];
  showSkeletons.value = true;
  
  // Vider le cache
  clearCache();
  
  console.log('✅ Skeletons displayed, cache cleared, waiting for services to start');
}

// === HANDLER POUR DÉSACTIVATION ANTICIPÉE ===
async function handleMultiroomDisabling(event) {
  console.log('🚨 MULTIROOM DISABLING EVENT REÇU');
  
  // Activer le flag de désactivation dans le store
  unifiedStore.setMultiroomDeactivating(true);
  
  // Fade-out synchronisé de TOUS les clients actuels
  await fadeOutAllClients();
  showSkeletons.value = false;
  
  // Vider le cache
  clearCache();
  
  console.log('✅ Waiting for backend confirmation to reset isDeactivating flag');
  // Note: isDeactivating sera réinitialisé dans le watcher quand isMultiroomActive devient false
}

// === LIFECYCLE ===
onMounted(async () => {
  // NOUVEAU : Reset des flags de transition si incohérents avec l'état réel
  // (Cas : modal fermée pendant une transition, puis réouverte)
  const currentMultiroomState = isMultiroomActive.value;
  
  if (unifiedStore.isMultiroomTransitioning && currentMultiroomState) {
    // Multiroom actif mais flag "activating" encore à true → reset
    console.log('⚠️ Reset isMultiroomTransitioning (multiroom already active)');
    unifiedStore.setMultiroomTransitioning(false);
  }
  
  if (unifiedStore.isMultiroomDeactivating && !currentMultiroomState) {
    // Multiroom inactif mais flag "deactivating" encore à true → reset
    console.log('⚠️ Reset isMultiroomDeactivating (multiroom already inactive)');
    unifiedStore.setMultiroomDeactivating(false);
  }
  
  // Charger les clients si multiroom actif
  if (isMultiroomActive.value) {
    await loadSnapcastClients();
  }

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
});

// === WATCHER POUR ACTIVATION/DÉSACTIVATION ===
watch(isMultiroomActive, async (newValue, oldValue) => {
  if (newValue && !oldValue) {
    // ACTIVATION : services démarrés, charger les clients
    console.log('✅ Multiroom services started, loading clients');
    unifiedStore.setMultiroomDeactivating(false);
    
    // forceNoCache si on est en transition (skeletons déjà affichés)
    const forceNoCache = unifiedStore.isMultiroomTransitioning;
    await loadSnapcastClients(forceNoCache);
    
    // Reset du flag de transition dans le store
    unifiedStore.setMultiroomTransitioning(false);
  } else if (!newValue && oldValue) {
    // DÉSACTIVATION : backend a confirmé, réinitialiser le flag
    console.log('✅ Multiroom disabled confirmed, resetting isDeactivating flag');
    unifiedStore.setMultiroomDeactivating(false);
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