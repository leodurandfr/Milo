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

// Surveillance des changements d'état audio
watch(() => audioStore.currentState, async (newState, oldState) => {
  if (newState === 'macos' && oldState !== 'macos') {
    // Activation de la source MacOS
    console.log("🔄 Source MacOS activée - Chargement initial de l'état");
    initialLoading.value = true;
    try {
      await snapclientStore.fetchStatus(true);
    } catch (err) {
      console.error("❌ Erreur lors du chargement initial:", err);
      errorState.value = true;
    } finally {
      initialLoading.value = false;
    }
  } else if (oldState === 'macos' && newState !== 'macos') {
    // Désactivation de la source MacOS
    snapclientStore.reset();
  }
});

// Surveiller l'état de connexion WebSocket
watch(wsConnected, (connected) => {
  console.log(`⚡ État WebSocket changé: ${connected ? 'connecté' : 'déconnecté'}`);
  if (connected && audioStore.currentState === 'macos' && (errorState.value || !snapclientStore.isActive)) {
    refreshStatus(false);
  }
});

// Surveiller les erreurs
watch(() => snapclientStore.error, (newError) => {
  if (newError) {
    console.error("⚠️ Erreur dans le store Snapclient:", newError);
    errorState.value = true;
  } else {
    errorState.value = false;
  }
});

// Références pour les fonctions de désabonnement
let unsubscribeMonitorConnected = null;
let unsubscribeMonitorDisconnected = null;
let unsubscribeServerEvent = null;
let unsubscribeAudioStatus = null;
let handleConnectionChange = null;

onMounted(async () => {
  // Chargement initial
  console.log("🔄 Chargement initial du statut Snapclient");
  initialLoading.value = true;
  try {
    await snapclientStore.fetchStatus(true);
    errorState.value = false;
  } catch (err) {
    console.error("❌ Erreur lors du chargement initial:", err);
    errorState.value = true;
  } finally {
    initialLoading.value = false;
  }

  // Écouter l'événement personnalisé de changement de connexion
  handleConnectionChange = (event) => {
    console.log("⚡ Événement de changement de connexion détecté:", event.detail);
  };
  window.addEventListener('snapclient-connection-changed', handleConnectionChange);

  // Moniteur connecté
  unsubscribeMonitorConnected = on('snapclient_monitor_connected', (data) => {
    console.log("⚡ Moniteur connecté au serveur:", data.host);
    // Mettre à jour l'état dans le store sans appel API
    snapclientStore.updateFromWebSocketEvent('snapclient_monitor_connected', data);
  });

  // Moniteur déconnecté
  unsubscribeMonitorDisconnected = on('snapclient_monitor_disconnected', (data) => {
    console.log("⚡ Moniteur déconnecté du serveur:", data.host, data.reason);
    // Mise à jour immédiate sans vérification
    snapclientStore.updateFromWebSocketEvent('snapclient_monitor_disconnected', data);
  });
  
  // Événements serveur
  unsubscribeServerEvent = on('snapclient_server_event', (data) => {
    console.log("⚡ Événement serveur reçu:", data);
  });
  
  // Disparition du serveur
  on('snapclient_server_disappeared', (data) => {
    console.log("🚨 Serveur Snapcast disparu:", data);
    // Mise à jour directe sans vérification
    snapclientStore.updateFromWebSocketEvent('snapclient_server_disappeared', data);
  });
  
  // Mises à jour d'état audio
  unsubscribeAudioStatus = on('audio_status_updated', (data) => {
    if (data.source === 'snapclient') {
      console.log("⚡ État audio mis à jour:", data.plugin_state);
      // Mise à jour directe sans vérification
      snapclientStore.updateFromStateEvent(data);
    }
  });
});

onUnmounted(() => {
  // Désinscription des événements
  if (unsubscribeMonitorConnected) unsubscribeMonitorConnected();
  if (unsubscribeMonitorDisconnected) unsubscribeMonitorDisconnected();
  if (unsubscribeServerEvent) unsubscribeServerEvent();
  if (unsubscribeAudioStatus) unsubscribeAudioStatus();
  
  // Désabonnement de l'événement personnalisé
  if (handleConnectionChange) {
    window.removeEventListener('snapclient-connection-changed', handleConnectionChange);
  }
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