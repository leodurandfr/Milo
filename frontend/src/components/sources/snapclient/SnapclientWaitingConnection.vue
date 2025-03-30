<template>
  <div class="waiting-connection">
    <h2>En attente de connexion MacOS</h2>
    <p>Attendez qu'un Mac se connecte via Snapcast...</p>
    
    <button 
      @click="discoverServers" 
      class="discover-button"
      :disabled="isLoading"
    >
      Rechercher des serveurs
    </button>
    
    <div v-if="discoveredServers.length > 0 && !isConnected" class="servers-list">
      <h3>Serveurs disponibles</h3>
      <ul>
        <li v-for="server in discoveredServers" :key="server.host" class="server-item">
          <span>{{ server.name }}</span>
          <button 
            @click="connectToServer(server.host)" 
            :disabled="isLoading"
            class="connect-button"
          >
            Connecter
          </button>
        </li>
      </ul>
    </div>
    
    <div v-if="error" class="error-message">
      {{ error }}
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue';
import { useSnapclientStore } from '@/stores/snapclient';
import { useAudioStore } from '@/stores/index';
import useWebSocket from '@/services/websocket';

const snapclientStore = useSnapclientStore();
const audioStore = useAudioStore();
const { on } = useWebSocket();

// Extraire les propriétés du store
const error = computed(() => snapclientStore.error);
const isLoading = computed(() => snapclientStore.isLoading);
const isActive = computed(() => snapclientStore.isActive);
const discoveredServers = computed(() => snapclientStore.discoveredServers);
const isConnected = computed(() => snapclientStore.isConnected);

// Flag pour éviter les découvertes multiples
const discoveryInProgress = ref(false);
const lastServerUpdate = ref(0);

// Surveiller les changements dans les serveurs découverts
watch(discoveredServers, (newServers) => {
  console.log(`📊 Serveurs détectés mis à jour: ${newServers.length} serveurs disponibles`);
  // Forcer la réactivité en cas de changement
  lastServerUpdate.value = Date.now();
}, { deep: true });

// Surveiller les changements d'état de connexion
watch(isConnected, (newConnected) => {
  console.log(`📊 État de connexion mis à jour: ${newConnected ? 'connecté' : 'déconnecté'}`);
});

// Références pour les fonctions de désabonnement
let unsubscribeServerFound = null;
let unsubscribeMonitorConnected = null;
let unsubscribeServerEvent = null;

// Actions
async function discoverServers() {
  console.log("🔍 Recherche manuelle de serveurs lancée");
  
  if (discoveryInProgress.value) {
    console.log("⚠️ Découverte déjà en cours, ignorée");
    return;
  }
  
  discoveryInProgress.value = true;
  
  try {
    if (!isActive.value) {
      console.log("⚠️ Découverte ignorée - plugin inactif");
      return;
    }
    
    const result = await snapclientStore.discoverServers();
    
    if (result && result.inactive === true) {
      console.log("⚠️ Résultat de découverte ignoré - plugin devenu inactif");
      return;
    }
    
    console.log(`✅ ${result?.servers?.length || 0} serveurs trouvés`);
    
    // Vérifier s'il y a des serveurs et forcer le rafraîchissement
    if (result?.servers?.length > 0) {
      console.log("🔄 Mise à jour forcée de la liste des serveurs");
      // Réinitialiser le store avec les nouveaux serveurs
      snapclientStore.$patch({
        discoveredServers: result.servers
      });
    }
  } catch (err) {
    console.error('❌ Erreur lors de la découverte des serveurs:', err);
  } finally {
    discoveryInProgress.value = false;
  }
}

async function connectToServer(serverHost) {
  console.log(`🔌 Tentative de connexion à ${serverHost}`);
  
  try {
    await snapclientStore.connectToServer(serverHost);
    
    // Force une mise à jour du statut après la connexion
    setTimeout(() => {
      snapclientStore.fetchStatus(true);
    }, 500);
    
  } catch (err) {
    console.error(`❌ Erreur lors de la connexion à ${serverHost}:`, err);
  }
}

onMounted(async () => {
  // Récupérer le statut initial avec force=true pour garantir une mise à jour complète
  await snapclientStore.fetchStatus(true);
  
  // Si aucun serveur n'est trouvé, lancer une découverte initiale
  if (discoveredServers.value.length === 0) {
    console.log("🔍 Aucun serveur dans l'état initial, lancement de la découverte");
    await discoverServers();
  }
  
  // Écouter les événements de découverte de serveurs via WebSocket
  unsubscribeServerFound = on('snapclient_server_event', (data) => {
    console.log("⚡ Événement serveur reçu dans WaitingConnection", data);
    
    // Vérifier si c'est un événement pertinent pour la découverte
    if (data && (data.method === "Client.OnConnect" || data.method === "Client.OnDisconnect" || 
                data.method === "Server.OnUpdate")) {
      
      console.log("🔄 Lancement d'une découverte suite à un événement serveur");
      // Force une découverte pour mettre à jour la liste
      discoverServers();
      
      // Force une mise à jour du statut pour détecter les connexions
      snapclientStore.fetchStatus(true);
    }
  });
  
  // Écouter les événements de connexion du moniteur
  unsubscribeMonitorConnected = on('snapclient_monitor_connected', (data) => {
    console.log("⚡ Moniteur connecté dans WaitingConnection", data);
    
    // Forcer une mise à jour du statut quand un moniteur se connecte
    if (audioStore.currentState === 'macos') {
      console.log("🔄 Mise à jour du statut suite à la connexion du moniteur");
      snapclientStore.fetchStatus(true);
    }
  });
  
  // Écouter les événements audio_status_updated pour détecter les changements d'état
  unsubscribeServerEvent = on('audio_status_updated', (data) => {
    if (data.source === 'snapclient') {
      console.log("⚡ Mise à jour d'état audio reçue:", data);
      
      // Force une mise à jour du statut
      snapclientStore.fetchStatus(true);
    }
  });
  
  // Lancer une découverte toutes les 10 secondes si aucun serveur n'est trouvé
  const discoveryInterval = setInterval(() => {
    if (audioStore.currentState === 'macos' && !isConnected.value && discoveredServers.value.length === 0 && !discoveryInProgress.value) {
      console.log("🔄 Découverte périodique (aucun serveur trouvé)");
      discoverServers();
    }
  }, 10000);
  
  // Nettoyage à la destruction
  onUnmounted(() => {
    if (unsubscribeServerFound) unsubscribeServerFound();
    if (unsubscribeMonitorConnected) unsubscribeMonitorConnected();
    if (unsubscribeServerEvent) unsubscribeServerEvent();
    clearInterval(discoveryInterval);
  });
});
</script>

<style scoped>
.waiting-connection {
  padding: 1rem;
  max-width: 600px;
  margin: 0 auto;
  text-align: center;
}

.discover-button {
  background-color: #2980b9;
  color: white;
  border: none;
  border-radius: 4px;
  padding: 8px 16px;
  margin: 10px 0;
  cursor: pointer;
}

.discover-button:disabled {
  background-color: #bdc3c7;
  cursor: not-allowed;
}

.error-message {
  background-color: #e74c3c;
  color: white;
  padding: 10px;
  margin: 10px 0;
}

.servers-list {
  margin-top: 1rem;
  text-align: left;
}

.server-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0.5rem;
  margin-bottom: 0.5rem;
  background-color: #f8f9fa;
  border-radius: 4px;
}

.connect-button {
  background-color: #27ae60;
  color: white;
  border: none;
  border-radius: 4px;
  padding: 4px 8px;
  cursor: pointer;
}

.connect-button:hover:not(:disabled) {
  background-color: #219653;
}

.connect-button:disabled {
  background-color: #bdc3c7;
  cursor: not-allowed;
}
</style>