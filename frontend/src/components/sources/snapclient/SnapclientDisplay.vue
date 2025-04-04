<template>
  <div class="snapclient-display">
    <!-- État de chargement initial uniquement -->
    <div v-if="initialLoading" class="loading-state">
      <div class="loading-spinner"></div>
      <p>Chargement de l'état Snapclient...</p>
    </div>
    
    <!-- Erreur websocket -->
    <div v-else-if="!wsConnected" class="error-state">
      <h3>Connexion au serveur perdue</h3>
      <p>La connexion WebSocket au serveur oakOS est interrompue. Vérifiez que le serveur backend est en cours d'exécution.</p>
      <button @click="refreshStatus" class="retry-button">Réessayer</button>
    </div>
    
    <!-- Erreur état -->
    <div v-else-if="errorState" class="error-state">
      <h3>Erreur lors du chargement de Snapclient</h3>
      <p>{{ snapclientStore.error || 'Une erreur s\'est produite. Veuillez réessayer.' }}</p>
      <button @click="refreshStatus" class="retry-button">Réessayer</button>
    </div>
    
    <!-- États normaux -->
    <template v-else>
      <SnapclientConnectionInfo v-if="isConnected" />
      <SnapclientWaitingConnection v-else />
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, watch, ref } from 'vue';
import { useAudioStore } from '@/stores/index';
import { useSnapclientStore } from '@/stores/snapclient';
import SnapclientWaitingConnection from './SnapclientWaitingConnection.vue';
import SnapclientConnectionInfo from './SnapclientConnectionInfo.vue';
import useWebSocket from '@/services/websocket';

const { on, isConnected: wsConnected } = useWebSocket();
const audioStore = useAudioStore();
const snapclientStore = useSnapclientStore();

// États locaux
const initialLoading = ref(true);
const errorState = ref(false);
let connectionCheckInterval = null;
let lastStatusCheckTime = ref(0);

// Références pour les fonctions de désabonnement - DÉFINIR ICI
const unsubscribeMonitorConnected = ref(null);
const unsubscribeMonitorDisconnected = ref(null);
const unsubscribeServerEvent = ref(null);
const unsubscribeAudioStatus = ref(null);
const unsubscribeServerDisappeared = ref(null);

// État dérivé pour contrôler l'affichage
const isConnected = computed(() => {
  return snapclientStore.isConnected && snapclientStore.pluginState === 'connected';
});

// Fonction pour rafraîchir le statut
async function refreshStatus(showLoader = false) {
  if (showLoader) {
    initialLoading.value = true;
  }
  
  errorState.value = false;
  lastStatusCheckTime.value = Date.now();
  
  try {
    await snapclientStore.fetchStatus(true);
    console.log("✅ Statut rafraîchi avec succès");
  } catch (err) {
    console.error("❌ Erreur lors du rafraîchissement du statut:", err);
    errorState.value = true;
  } finally {
    initialLoading.value = false;
  }
}

// Vérifie l'état de la connexion périodiquement
function startConnectionCheck() {
  // Nettoyer l'intervalle existant s'il y en a un
  if (connectionCheckInterval) {
    clearInterval(connectionCheckInterval);
  }
  
  // Vérifier la connexion périodiquement
  connectionCheckInterval = setInterval(async () => {
    // Ne vérifier que si nous sommes censés être connectés
    if (isConnected.value && audioStore.currentState === 'macos') {
      console.log("🔍 Vérification périodique de la connexion Snapclient");
      
      try {
        // Éviter les vérifications trop fréquentes
        if (Date.now() - lastStatusCheckTime.value < 2000) {
          return;
        }
        
        lastStatusCheckTime.value = Date.now();
        const status = await snapclientStore.fetchStatus(false);
        
        // Si déconnecté, mettre à jour l'interface
        if (!status.device_connected && snapclientStore.isConnected) {
          console.log("🔴 Déconnexion détectée lors de la vérification périodique");
          snapclientStore.forceDisconnect("periodic_check");
        }
      } catch (err) {
        console.error("🔴 Erreur lors de la vérification périodique", err);
        // En cas d'erreur, forcer la déconnexion pour mettre à jour l'UI
        snapclientStore.forceDisconnect("check_error");
      }
    }
  }, 3000); // Vérifier toutes les 3 secondes
}

// Surveiller l'état de connexion pour mettre à jour l'UI
watch(isConnected, (connected) => {
  console.log(`⚡ État de connexion Snapclient changé: ${connected}`);
  
  // Mettre à jour l'UI immédiatement selon l'état de connexion
  if (!connected && snapclientStore.pluginState !== 'connected') {
    // Force l'actualisation si on passe de connecté à déconnecté
    console.log("🔁 Forcer l'actualisation de l'UI après déconnexion");
  }
});

// Surveillance des changements d'état audio
watch(() => audioStore.currentState, async (newState, oldState) => {
  if (newState === 'macos' && oldState !== 'macos') {
    // Activation de la source MacOS
    console.log("🔄 Source MacOS activée - Chargement initial de l'état");
    initialLoading.value = true;
    try {
      await snapclientStore.fetchStatus(true);
      // Démarrer la vérification périodique quand on active MacOS
      startConnectionCheck();
    } catch (err) {
      console.error("❌ Erreur lors du chargement initial:", err);
      errorState.value = true;
    } finally {
      initialLoading.value = false;
    }
  } else if (oldState === 'macos' && newState !== 'macos') {
    // Désactivation de la source MacOS
    if (connectionCheckInterval) {
      clearInterval(connectionCheckInterval);
      connectionCheckInterval = null;
    }
    snapclientStore.reset();
  }
});

onMounted(async () => {
  // Chargement initial
  console.log("🔄 Chargement initial du statut Snapclient");
  initialLoading.value = true;
  try {
    await snapclientStore.fetchStatus(true);
    errorState.value = false;
    
    // Démarrer la vérification périodique si on est sur MacOS
    if (audioStore.currentState === 'macos') {
      startConnectionCheck();
    }
  } catch (err) {
    console.error("❌ Erreur lors du chargement initial:", err);
    errorState.value = true;
  } finally {
    initialLoading.value = false;
  }

  // S'abonner aux événements
  unsubscribeMonitorConnected.value = on('snapclient_monitor_connected', (data) => {
    console.log("⚡ Moniteur connecté au serveur:", data.host);
    snapclientStore.updateFromWebSocketEvent('snapclient_monitor_connected', data);
  });

  unsubscribeMonitorDisconnected.value = on('snapclient_monitor_disconnected', (data) => {
    console.log("⚡ Moniteur déconnecté du serveur:", data.host, data.reason);
    snapclientStore.updateFromWebSocketEvent('snapclient_monitor_disconnected', data);
    
    // Forcer un refresh après un court délai
    setTimeout(() => refreshStatus(false), 200);
  });
  
  // Événements serveur
  unsubscribeServerEvent.value = on('snapclient_server_event', (data) => {
    console.log("⚡ Événement serveur reçu:", data);
  });
  
  // Disparition du serveur
  unsubscribeServerDisappeared.value = on('snapclient_server_disappeared', (data) => {
    console.log("🚨 Serveur Snapcast disparu:", data);
    snapclientStore.updateFromWebSocketEvent('snapclient_server_disappeared', data);
    
    // Forcer un refresh après un court délai
    setTimeout(() => refreshStatus(false), 200);
  });
  
  // Mises à jour d'état audio
  unsubscribeAudioStatus.value = on('audio_status_updated', (data) => {
    if (data.source === 'snapclient') {
      console.log("⚡ État audio mis à jour:", data.plugin_state);
      snapclientStore.updateFromStateEvent(data);
    }
  });
});

onUnmounted(() => {
  // Nettoyer l'intervalle de vérification périodique
  if (connectionCheckInterval) {
    clearInterval(connectionCheckInterval);
    connectionCheckInterval = null;
  }
  
  // Désinscription des événements
  if (unsubscribeMonitorConnected.value) unsubscribeMonitorConnected.value();
  if (unsubscribeMonitorDisconnected.value) unsubscribeMonitorDisconnected.value();
  if (unsubscribeServerEvent.value) unsubscribeServerEvent.value();
  if (unsubscribeAudioStatus.value) unsubscribeAudioStatus.value();
  if (unsubscribeServerDisappeared.value) unsubscribeServerDisappeared.value();
});
</script>

<style scoped>
.snapclient-display {
  width: 100%;
  padding: 1rem;
  display: flex;
  flex-direction: column;
  align-items: center;
}

.loading-state, .error-state {
  text-align: center;
  padding: 2rem;
  width: 100%;
  max-width: 500px;
  margin: 0 auto;
}

.loading-spinner {
  width: 40px;
  height: 40px;
  margin: 0 auto 1rem;
  border: 4px solid rgba(0, 0, 0, 0.1);
  border-radius: 50%;
  border-top: 4px solid #3498db;
  animation: spin 1s linear infinite;
}

.retry-button {
  background-color: #2196F3;
  color: white;
  border: none;
  border-radius: 4px;
  padding: 8px 16px;
  cursor: pointer;
  margin-top: 1rem;
}

.retry-button:hover {
  background-color: #0b7dda;
}

.error-state {
  background-color: #ffebee;
  border: 1px solid #ffcdd2;
  border-radius: 4px;
}

@keyframes spin {
  0% { transform: rotate(0deg); }
  100% { transform: rotate(360deg); }
}
</style>