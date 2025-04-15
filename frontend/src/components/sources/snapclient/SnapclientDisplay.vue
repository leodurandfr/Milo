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
      <p>La connexion WebSocket au serveur oakOS est interrompue.</p>
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
import { computed, onMounted, onUnmounted, ref, watch } from 'vue';
import { useAudioStore } from '@/stores/index';
import { useSnapclientStore } from '@/stores/snapclient';
import SnapclientWaitingConnection from './SnapclientWaitingConnection.vue';
import SnapclientConnectionInfo from './SnapclientConnectionInfo.vue';
import useWebSocket from '@/services/websocket';

const { on, isConnected: wsConnected } = useWebSocket();
const audioStore = useAudioStore();
const snapclientStore = useSnapclientStore();

// États locaux simplifiés
const initialLoading = ref(true);
const errorState = ref(false);
let connectionCheck = null;
const unsubscribeHandlers = [];

// État simplifié pour contrôler l'affichage
const isConnected = computed(() => snapclientStore.isConnected);

// Surveiller les changements d'état de connexion
watch(isConnected, (newConnected, oldConnected) => {
  if (oldConnected && !newConnected) {
    console.log("📊 Déconnexion détectée dans le watcher");
    // Éventuellement rafraîchir l'interface si nécessaire
  }
});

// Fonction pour rafraîchir le statut
async function refreshStatus(showLoader = false) {
  if (showLoader) initialLoading.value = true;
  errorState.value = false;
  
  try {
    await snapclientStore.fetchStatus(true);
  } catch (err) {
    console.error("Erreur lors du rafraîchissement du statut:", err);
    errorState.value = true;
  } finally {
    initialLoading.value = false;
  }
}

// Configurer les événements WebSocket de manière centralisée
function setupWebSocketEvents() {
  // Événements critiques qui nécessitent une notification immédiate
  ['snapclient_monitor_connected', 'snapclient_monitor_disconnected', 'snapclient_server_disappeared'].forEach(eventType => {
    const unsub = on(eventType, (data) => {
      console.log(`⚡ Événement WebSocket reçu: ${eventType}`, data);
      snapclientStore.updateFromWebSocketEvent(eventType, data);
    });
    unsubscribeHandlers.push(unsub);
  });
  
  // Écouter les mises à jour d'état audio
  const unsubAudio = on('audio_status_updated', (data) => {
    if (data.source === 'snapclient') {
      snapclientStore.updateFromStateEvent(data);
    }
  });
  unsubscribeHandlers.push(unsubAudio);
  
  // Ajouter un écouteur pour les événements globaux de déconnexion
  const handleGlobalDisconnect = (event) => {
    console.log("🔌 Événement global de déconnexion reçu", event.detail);
    // Rafraîchir immédiatement le statut
    refreshStatus(false);
  };
  
  document.addEventListener('snapclient-disconnected', handleGlobalDisconnect);
  unsubscribeHandlers.push(() => {
    document.removeEventListener('snapclient-disconnected', handleGlobalDisconnect);
  });
}

// Configurer la vérification périodique - plus fréquente et plus robuste
function setupPeriodicCheck() {
  return setInterval(async () => {
    if (audioStore.currentState === 'macos') {
      try {
        const result = await snapclientStore.fetchStatus(false);
        
        // Si la réponse indique une déconnexion mais qu'on pensait être connecté
        if (!result.device_connected && snapclientStore.isConnected) {
          console.log("🔴 Déconnexion détectée lors du check périodique");
          snapclientStore.forceDisconnect("periodic_check");
        }
      } catch (err) {
        console.error("Erreur de vérification périodique:", err);
        // En cas d'erreur, considérer comme déconnecté
        if (snapclientStore.isConnected) {
          snapclientStore.forceDisconnect("check_error");
        }
      }
    }
  }, 3000); // Vérification toutes les 3 secondes
}

onMounted(async () => {
  // Chargement initial
  initialLoading.value = true;
  
  try {
    await snapclientStore.fetchStatus(true);
    errorState.value = false;
    
    // Configurer les événements WebSocket
    setupWebSocketEvents();
    
    // Démarrer la vérification périodique
    connectionCheck = setupPeriodicCheck();
    
  } catch (err) {
    console.error("Erreur lors du chargement initial:", err);
    errorState.value = true;
  } finally {
    initialLoading.value = false;
  }
});

onUnmounted(() => {
  // Nettoyer l'intervalle
  if (connectionCheck) {
    clearInterval(connectionCheck);
  }
  
  // Nettoyer les abonnements WebSocket
  unsubscribeHandlers.forEach(unsub => unsub && unsub());
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