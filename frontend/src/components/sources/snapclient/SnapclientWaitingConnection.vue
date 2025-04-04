<template>
  <div class="waiting-connection">
    <h2>En attente de connexion MacOS</h2>
    <p>Attendez qu'un Mac se connecte via Snapcast...</p>
    
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
const isActive = computed(() => snapclientStore.isActive);
const isConnected = computed(() => snapclientStore.isConnected);
const lastServer = ref(null);

// Surveiller l'état de connexion
watch(isConnected, (newConnected) => {
  console.log(`📊 État de connexion mis à jour: ${newConnected ? 'connecté' : 'déconnecté'}`);
});

// Références pour les fonctions de désabonnement
let unsubscribeServerEvent = null;
let unsubscribeMonitorConnected = null;
let reconnectInterval = null;

// IMPORTANT: Déclarer tous les hooks de cycle de vie avant les opérations asynchrones
onUnmounted(() => {
  if (unsubscribeServerEvent) unsubscribeServerEvent();
  if (unsubscribeMonitorConnected) unsubscribeMonitorConnected();
  if (reconnectInterval) clearInterval(reconnectInterval);
});

// Fonction pour essayer de se reconnecter automatiquement
async function tryAutoReconnect() {
  try {
    // Vérifier d'abord si l'on est déjà connecté
    if (snapclientStore.isConnected) {
      console.log("✅ Déjà connecté, pas besoin de reconnexion automatique");
      return;
    }

    // Vérifier l'état actuel avant de lancer la découverte
    const statusResult = await snapclientStore.fetchStatus(false);
    if (snapclientStore.isConnected) {
      console.log("✅ Connexion détectée lors du check de statut, pas besoin de reconnexion");
      return;
    }

    // Rechercher d'abord des serveurs
    const result = await snapclientStore.discoverServers();
    
    if (!result || !result.servers || result.servers.length === 0) {
      console.log("🔍 Aucun serveur trouvé pour la reconnexion automatique");
      return;
    }
    
    console.log("🔍 Serveurs trouvés pour reconnexion:", result.servers);
    
    // Prioriser la reconnexion:
    // 1. Au dernier serveur connu s'il est disponible
    // 2. Sinon, au premier serveur disponible s'il n'y en a qu'un
    let serverToConnect = null;
    
    if (lastServer.value && lastServer.value.host) {
      // Vérifier si le dernier serveur est toujours disponible
      const foundLastServer = result.servers.find(s => s.host === lastServer.value.host);
      if (foundLastServer) {
        console.log("🔄 Dernier serveur utilisé trouvé, tentative de reconnexion:", foundLastServer.name);
        serverToConnect = foundLastServer.host;
      }
    }
    
    // Si aucun dernier serveur n'est trouvé mais qu'il y a exactement un serveur disponible
    if (!serverToConnect && result.servers.length === 1) {
      console.log("🔄 Un seul serveur disponible, tentative de connexion automatique:", result.servers[0].name);
      serverToConnect = result.servers[0].host;
    }
    
    // Se connecter si un serveur a été identifié et qu'on n'est pas déjà connecté
    if (serverToConnect && !snapclientStore.isConnected) {
      console.log("🔌 Connexion automatique à:", serverToConnect);
      await snapclientStore.connectToServer(serverToConnect);
      
      // Enregistrer ce serveur comme dernier serveur utilisé
      localStorage.setItem('lastSnapclientServer', JSON.stringify({
        host: serverToConnect,
        timestamp: Date.now()
      }));
    }
  } catch (err) {
    console.error("❌ Erreur lors de la tentative de reconnexion automatique:", err);
  }
}

onMounted(async () => {
  try {
    // Récupérer le statut initial
    await snapclientStore.fetchStatus(true);
    
    // S'il y a un dernier serveur enregistré, tenter de s'y reconnecter
    const savedServer = localStorage.getItem('lastSnapclientServer');
    if (savedServer && !isConnected.value) {
      try {
        const serverData = JSON.parse(savedServer);
        console.log("💾 Tentative de reconnexion au dernier serveur:", serverData.host);
        lastServer.value = serverData;
        
        // Tenter la reconnexion automatique
        await tryAutoReconnect();
      } catch (e) {
        console.error("❌ Erreur lors de la lecture du dernier serveur:", e);
      }
    }
    
    // Écouter les événements serveur pour la découverte automatique
    unsubscribeServerEvent = on('snapclient_server_event', (data) => {
      if (audioStore.currentState === 'macos' && !isConnected.value) {
        console.log("⚡ Événement serveur reçu:", data);
        
        // Déclencher la découverte et tentative de reconnexion automatique
        tryAutoReconnect();
      }
    });
    
    // Écouter les événements de découverte
    unsubscribeMonitorConnected = on('snapclient_monitor_connected', (data) => {
      console.log("⚡ Moniteur connecté:", data);
      
      // Tenter de se reconnecter au dernier serveur connu
      if (audioStore.currentState === 'macos' && !isConnected.value) {
        tryAutoReconnect();
      }
    });
    
    // Démarrer une vérification périodique pour la reconnexion automatique
    reconnectInterval = setInterval(() => {
      if (audioStore.currentState === 'macos' && !isConnected.value) {
        tryAutoReconnect();
      }
    }, 5000);
  } catch (error) {
    console.error("Erreur lors de l'initialisation:", error);
  }
});
</script>

<style scoped>
.waiting-connection {
  padding: 1rem;
  max-width: 600px;
  margin: 0 auto;
  text-align: center;
}

.error-message {
  background-color: #e74c3c;
  color: white;
  padding: 10px;
  margin: 10px 0;
}
</style>