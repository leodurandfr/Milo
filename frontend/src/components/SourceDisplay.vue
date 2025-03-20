<template>
  <div class="source-display">
    <!-- Lecteur Spotify (Librespot) -->
    <LibrespotPlayer v-if="currentSource === 'librespot'" />
    
    <!-- Pour Snapclient (MacOS via Snapcast) -->
    <DeviceConnectionInfo
      v-if="currentSource === 'macos' && isDeviceConnected"
      source="macos"
      :deviceInfo="deviceInfo"
      :isConnected="true"
      :showDiscovered="showDiscoveredServers"
      :discoveredDevices="discoveredServers"
      :connectionRequest="snapclientStore.currentRequest"
      :showConnectionRequest="snapclientStore.showConnectionRequest"
      @disconnect="disconnectSnapcast"
      @discover-devices="discoverSnapcastServers"
      @connect-device="connectToSnapcastServer"
      @accept-request="acceptSnapcastConnection"
      @reject-request="rejectSnapcastConnection"
      @cancel-request="dismissSnapcastRequest"
    >
      <template #actions>
        <button @click="toggleDiscoveredServers" class="refresh-button">
          {{ showDiscoveredServers ? 'Masquer' : 'Découvrir' }}
        </button>
      </template>
    </DeviceConnectionInfo>
    
    <!-- Pour Bluetooth -->
    <DeviceConnectionInfo
      v-else-if="currentSource === 'bluetooth' && isDeviceConnected"
      source="bluetooth"
      :deviceInfo="deviceInfo"
      :isConnected="true"
      @disconnect="disconnectBluetooth"
    />
    
    <!-- Attente de connexion pour les différentes sources -->
    <WaitingConnection
      v-else-if="currentSource === 'macos' && !isDeviceConnected" 
      sourceType="macos"
    />
    
    <WaitingConnection 
      v-else-if="currentSource === 'bluetooth' && !isDeviceConnected" 
      sourceType="bluetooth"
    />
    
    <WaitingConnection
      v-else-if="currentSource === 'webradio'"
      sourceType="webradio"
    />
  </div>
</template>

<script setup>
import { computed, ref, onMounted, watch } from 'vue';
import { useAudioStore } from '@/stores/index';
import { useSnapclientStore } from '@/stores/snapclient';
import LibrespotPlayer from './sources/librespot/LibrespotPlayer.vue';
import DeviceConnectionInfo from './sources/DeviceConnectionInfo.vue';
import WaitingConnection from './sources/WaitingConnection.vue';

const audioStore = useAudioStore();
const snapclientStore = useSnapclientStore();

// État local
const discoveredServers = ref([]);
const showDiscoveredServers = ref(false);

const currentSource = computed(() => audioStore.currentState);

const isDeviceConnected = computed(() => {
  // Pour chaque source, déterminer si un appareil est connecté
  if (currentSource.value === 'librespot') {
    return !audioStore.isDisconnected;
  } else if (currentSource.value === 'macos') {
    return snapclientStore.isConnected;
  } else if (currentSource.value === 'bluetooth') {
    return audioStore.metadata && audioStore.metadata.deviceConnected;
  }
  return false;
});

const deviceInfo = computed(() => {
  if (currentSource.value === 'macos') {
    return snapclientStore.deviceInfo;
  }
  return audioStore.metadata || {};
});

// Fonctions pour Snapclient
async function disconnectSnapcast() {
  await snapclientStore.disconnectFromServer();
}

async function discoverSnapcastServers() {
  showDiscoveredServers.value = true;
  const servers = await snapclientStore.discoverServers();
  discoveredServers.value = servers;
}

function toggleDiscoveredServers() {
  showDiscoveredServers.value = !showDiscoveredServers.value;
  if (showDiscoveredServers.value) {
    discoverSnapcastServers();
  }
}

async function connectToSnapcastServer(device) {
  const host = device.host || device;
  return await snapclientStore.connectToServer(host);
}

async function acceptSnapcastConnection() {
  if (snapclientStore.currentRequest) {
    await snapclientStore.acceptConnectionRequest(snapclientStore.currentRequest.host);
  }
}

async function rejectSnapcastConnection() {
  if (snapclientStore.currentRequest) {
    await snapclientStore.rejectConnectionRequest(snapclientStore.currentRequest.host);
  }
}

function dismissSnapcastRequest() {
  snapclientStore.dismissConnectionRequest();
}

// Fonction pour Bluetooth (à implémenter)
async function disconnectBluetooth() {
  await audioStore.controlSource('bluetooth', 'disconnect');
}

// Surveiller les changements de source pour effectuer des actions spécifiques
watch(() => currentSource.value, (newSource) => {
  if (newSource === 'macos') {
    // Lorsqu'on active la source MacOS, vérifier le statut
    snapclientStore.checkStatus();
    // Ne pas lancer automatiquement la découverte pour éviter de surcharger l'interface
    showDiscoveredServers.value = false;
  }
}, { immediate: true });

onMounted(() => {
  // Vérifier si on est sur la source MacOS au démarrage
  if (currentSource.value === 'macos') {
    snapclientStore.checkStatus();
  }
});
</script>

<style scoped>
.source-display {
  position: relative;
  width: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
}

.refresh-button {
  background-color: #0077cc;
  color: white;
  border: none;
  padding: 0.5rem 1rem;
  border-radius: 4px;
  cursor: pointer;
  transition: background-color 0.2s;
}

.refresh-button:hover {
  background-color: #0066b3;
}
</style>