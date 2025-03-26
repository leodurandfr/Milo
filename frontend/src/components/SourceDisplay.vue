<template>
  <div class="source-display">
    <!-- Lecteur Spotify (Librespot) -->
    <LibrespotPlayer v-if="currentSource === 'librespot'" />
    
    <!-- Pour Snapclient (MacOS via Snapcast) -->
    <DeviceConnectionInfo
      v-if="currentSource === 'macos' && isDeviceConnected"
      :key="'macos-connected-' + connectionUpdateCounter"
      source="macos"
      :deviceInfo="deviceInfo"
      :isConnected="isDeviceConnectedOverride || snapclientStore.isConnected"
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
        <button @click="forceRefreshSnapclientStatus" class="refresh-button">
          Actualiser
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
import { computed, ref, onMounted, watch, onUnmounted, nextTick } from 'vue';
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
const checkServerInterval = ref(null);
const lastCheckTime = ref(0);
const isDeviceConnectedOverride = ref(false);
const statusCheckTimer = ref(null);
const disconnectionCheckTimer = ref(null);
const reconnectInProgressTimer = ref(null);
const reconnectCheckInterval = ref(null);
const connectionUpdateCounter = ref(0); // Pour forcer le rendu du composant
const lastSnapclientStatus = ref({});

const currentSource = computed(() => audioStore.currentState);

// Réinitialisation de l'état lors d'une déconnexion
function resetConnectionState() {
  console.log("Réinitialisation de l'état de connexion");
  isDeviceConnectedOverride.value = false;
  // Incrémenter le compteur pour forcer le rendu
  incrementUpdateCounter();
}

// Forcer une mise à jour de l'interface
function incrementUpdateCounter() {
  connectionUpdateCounter.value++;
}

// Fonction pour forcer la mise à jour du statut
async function forceRefreshSnapclientStatus() {
  if (currentSource.value === 'macos') {
    console.log("Forçage du rafraîchissement du statut snapclient");
    const status = await snapclientStore.checkStatus();
    console.log("Statut snapclient récupéré:", status);
    
    // Stocker le dernier statut pour comparaison
    lastSnapclientStatus.value = { ...snapclientStore.deviceInfo, connected: status };
    
    // Si on est connecté mais que l'UI ne le montre pas, forcer une mise à jour
    if (snapclientStore.isConnected && !isDeviceConnected.value) {
      console.log("Correction de l'état connecté");
      isDeviceConnectedOverride.value = true;
      incrementUpdateCounter();
    } else if (!snapclientStore.isConnected && isDeviceConnectedOverride.value) {
      // Si snapclient est déconnecté mais que l'UI montre connecté, corriger
      resetConnectionState();
    }
    
    return status;
  }
  return false;
}

// Vérifier spécifiquement si une déconnexion s'est produite
async function checkDisconnection() {
  // Si on est sur la source MacOS et qu'on pense être connecté
  if (currentSource.value === 'macos' && (isDeviceConnectedOverride.value || isDeviceConnected.value)) {
    // Vérifier le statut
    const status = await snapclientStore.checkStatus();
    
    // Si le backend dit qu'on est déconnecté mais que le frontend montre connecté
    if (!status && (isDeviceConnectedOverride.value || isDeviceConnected.value)) {
      console.log("Déconnexion détectée, mise à jour de l'interface");
      resetConnectionState();
      
      // Forcer une recherche immédiate de nouveaux serveurs
      setTimeout(() => {
        discoverSnapcastServers();
      }, 500);
    }
  }
}

// Vérifier s'il y a eu une reconnexion automatique
async function checkReconnection() {
  if (currentSource.value === 'macos' && !isDeviceConnected.value) {
    // Forcer une vérification du statut
    const status = await snapclientStore.checkStatus();
    
    // Si snapclient est connecté mais que l'UI montre déconnecté, corriger
    if (status && !isDeviceConnected.value) {
      console.log("Reconnexion automatique détectée, mise à jour de l'interface");
      isDeviceConnectedOverride.value = true;
      incrementUpdateCounter();
      
      // Annuler toute recherche de serveurs en cours
      showDiscoveredServers.value = false;
    }
  }
}

const isDeviceConnected = computed(() => {
  // Si on a un override actif, toujours retourner true
  if (isDeviceConnectedOverride.value && currentSource.value === 'macos') {
    return true;
  }
  
  // Pour chaque source, déterminer si un appareil est connecté
  if (currentSource.value === 'librespot') {
    return !audioStore.isDisconnected;
  } else if (currentSource.value === 'macos') {
    return snapclientStore.isConnected || 
           (snapclientStore.deviceInfo && Object.keys(snapclientStore.deviceInfo).length > 0) ||
           deviceInfo.value.connected === true;
  } else if (currentSource.value === 'bluetooth') {
    return audioStore.metadata && audioStore.metadata.deviceConnected;
  }
  return false;
});

const deviceInfo = computed(() => {
  if (currentSource.value === 'macos') {
    // Si nous avons des données de deviceInfo, utilisons-les même si isConnected est false
    if (snapclientStore.deviceInfo && Object.keys(snapclientStore.deviceInfo).length > 0) {
      // Force la propriété connected à true si on a des infos d'appareil
      return {
        ...snapclientStore.deviceInfo,
        connected: true,
        deviceConnected: true
      };
    }
    return snapclientStore.deviceInfo || {};
  }
  return audioStore.metadata || {};
});

// Fonctions pour Snapclient
async function disconnectSnapcast() {
  isDeviceConnectedOverride.value = false;
  await snapclientStore.disconnectFromServer();
  // Forcer une mise à jour immédiate de l'interface
  resetConnectionState();
  // Attendre un peu et forcer une recherche de serveurs
  setTimeout(() => {
    discoverSnapcastServers();
  }, 500);
}

async function discoverSnapcastServers() {
  showDiscoveredServers.value = true;
  const servers = await snapclientStore.discoverServers();
  discoveredServers.value = servers;
  lastCheckTime.value = Date.now();
  
  // Si des serveurs sont trouvés mais qu'on n'est pas connecté, tenter une reconnexion
  if (servers.length > 0 && !isDeviceConnected.value && !reconnectInProgressTimer.value) {
    // Éviter les reconnexions multiples en utilisant un timer
    reconnectInProgressTimer.value = setTimeout(async () => {
      if (!isDeviceConnected.value) {
        console.log("Serveur trouvé après recherche, tentative de connexion:", servers[0].host);
        await connectToSnapcastServer(servers[0]);
      }
      reconnectInProgressTimer.value = null;
    }, 1000);
  }
  
  return servers;
}

function toggleDiscoveredServers() {
  showDiscoveredServers.value = !showDiscoveredServers.value;
  if (showDiscoveredServers.value) {
    discoverSnapcastServers();
  }
}

async function connectToSnapcastServer(device) {
  const host = device.host || device;
  const result = await snapclientStore.connectToServer(host);
  
  // Si la connexion réussit, forcer une vérification immédiate
  if (result) {
    isDeviceConnectedOverride.value = true;
    incrementUpdateCounter();
    
    // Masquer la liste des serveurs
    showDiscoveredServers.value = false;
    
    // Vérification supplémentaire après un délai
    setTimeout(() => {
      forceRefreshSnapclientStatus();
    }, 1000);
  }
  
  return result;
}

async function acceptSnapcastConnection() {
  if (snapclientStore.currentRequest) {
    const result = await snapclientStore.acceptConnectionRequest(snapclientStore.currentRequest.host);
    if (result) {
      isDeviceConnectedOverride.value = true;
      incrementUpdateCounter();
    }
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

// Fonction pour Bluetooth
async function disconnectBluetooth() {
  await audioStore.controlSource('bluetooth', 'disconnect');
}

// Fonction de vérification périodique qui cherche activement des serveurs
// quand on est déconnecté, et voit si un nouveau snapserver est disponible
async function periodicServerCheck() {
  // Si on n'est pas sur la source macos, ne rien faire
  if (currentSource.value !== 'macos') {
    return;
  }
  
  // Si le dernier check est trop récent (<8s), ne pas vérifier à nouveau
  const now = Date.now();
  if (now - lastCheckTime.value < 8000) {
    return;
  }
  
  console.log("Vérification périodique des serveurs Snapcast...");
  
  // Forcer la mise à jour du statut d'abord
  await forceRefreshSnapclientStatus();
  
  // Si toujours pas connecté, chercher des serveurs
  if (!isDeviceConnected.value) {
    const servers = await discoverSnapcastServers();
    
    // Si un serveur est trouvé et qu'on n'est pas connecté, tenter une connexion
    // (cette logique est maintenant dans discoverSnapcastServers)
  }
}

// Écouter les événements WebSocket directement
function setupWebSocketEvents() {
  window.addEventListener('snapclient_disconnected', (event) => {
    console.log("Événement WebSocket de déconnexion snapclient reçu");
    resetConnectionState();
    
    // Attendre un peu et forcer une recherche de serveurs
    setTimeout(() => {
      discoverSnapcastServers();
    }, 1000);
  });
  
  window.addEventListener('snapclient_server_restarted', (event) => {
    console.log("Événement WebSocket de redémarrage serveur snapclient reçu");
    // Forcer une vérification immédiate
    setTimeout(() => {
      forceRefreshSnapclientStatus();
      if (!isDeviceConnected.value) {
        discoverSnapcastServers();
      }
    }, 500);
  });
}

watch(
  () => [snapclientStore.isConnected, currentSource.value],
  ([newIsConnected, newSource]) => {
    if (newSource === 'macos') {
      console.log("État de connexion snapclient modifié:", newIsConnected);
      
      // Si on est connecté, s'assurer que notre override est aussi actif
      if (newIsConnected) {
        isDeviceConnectedOverride.value = true;
        incrementUpdateCounter();
      } else {
        // Si on est déconnecté, réinitialiser l'override immédiatement
        resetConnectionState();
        
        // Puis vérifier s'il y a de nouveaux serveurs disponibles
        setTimeout(() => {
          discoverSnapcastServers();
        }, 1000);
      }
    }
  },
  { immediate: true }
);

// Observer les changements de deviceInfo
watch(() => snapclientStore.deviceInfo, (newVal, oldVal) => {
  if (currentSource.value === 'macos' && newVal && 
      JSON.stringify(newVal) !== JSON.stringify(oldVal)) {
    console.log("DeviceInfo de snapclient a changé:", newVal);
    // Forcer une mise à jour de l'interface
    nextTick(() => {
      incrementUpdateCounter();
    });
  }
}, { deep: true });

// Surveiller les changements de source
watch(() => currentSource.value, (newSource) => {
  if (newSource === 'macos') {
    // Lorsqu'on active la source MacOS, vérifier le statut
    snapclientStore.checkStatus();
    // Forcer une découverte initiale
    discoverSnapcastServers();
    
    // Redémarrer les timers si nécessaire
    if (!statusCheckTimer.value) {
      statusCheckTimer.value = setInterval(() => {
        if (currentSource.value === 'macos') {
          forceRefreshSnapclientStatus();
        }
      }, 3000);
    }
    
    if (!disconnectionCheckTimer.value) {
      disconnectionCheckTimer.value = setInterval(() => {
        if (currentSource.value === 'macos') {
          checkDisconnection();
        }
      }, 2000);
    }
    
    if (!reconnectCheckInterval.value) {
      reconnectCheckInterval.value = setInterval(() => {
        if (currentSource.value === 'macos' && !isDeviceConnected.value) {
          checkReconnection();
        }
      }, 2500);
    }
  } else {
    // Si on change de source, réinitialiser le flag d'override
    isDeviceConnectedOverride.value = false;
  }
}, { immediate: true });

onMounted(() => {
  // Configuration des événements WebSocket
  setupWebSocketEvents();
  
  // Vérifier si on est sur la source MacOS au démarrage
  if (currentSource.value === 'macos') {
    snapclientStore.checkStatus();
    discoverSnapcastServers();
    
    // Démarrer la vérification périodique des serveurs
    checkServerInterval.value = setInterval(periodicServerCheck, 5000);
    
    // Démarrer la vérification du statut spécifique
    statusCheckTimer.value = setInterval(() => {
      if (currentSource.value === 'macos') {
        forceRefreshSnapclientStatus();
      }
    }, 3000);
    
    // Ajouter une vérification spécifique pour les déconnexions
    disconnectionCheckTimer.value = setInterval(() => {
      if (currentSource.value === 'macos') {
        checkDisconnection();
      }
    }, 2000);
    
    // Ajouter une vérification spécifique pour les reconnexions automatiques
    reconnectCheckInterval.value = setInterval(() => {
      if (currentSource.value === 'macos' && !isDeviceConnected.value) {
        checkReconnection();
      }
    }, 2500);
    
    // Faire une première vérification après chargement
    setTimeout(forceRefreshSnapclientStatus, 500);
    
    // Et une deuxième quelques secondes plus tard pour être sûr
    setTimeout(forceRefreshSnapclientStatus, 3000);
  }
});

onUnmounted(() => {
  // Nettoyer les intervalles lors du démontage
  [checkServerInterval, statusCheckTimer, disconnectionCheckTimer, 
   reconnectInProgressTimer, reconnectCheckInterval].forEach(timer => {
    if (timer && timer.value) {
      clearInterval(timer.value);
    }
  });
  
  // Supprimer les écouteurs d'événements
  window.removeEventListener('snapclient_disconnected', () => {});
  window.removeEventListener('snapclient_server_restarted', () => {});
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
  margin-left: 0.5rem;
  font-size: 0.9rem;
}

.refresh-button:hover {
  background-color: #0066b3;
}
</style>