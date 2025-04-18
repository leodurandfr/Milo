<template>
  <!-- Utiliser la condition sur audioStore.currentState -->
  <div v-if="audioStore.currentState === 'macos'" class="snapclient-component">
      <!-- État "ready" - En attente de connexion -->
      <div v-if="!isConnected" class="waiting-state">
          <h2>En attente de connexion MacOS</h2>
          <p>Attendez qu'un Mac se connecte via Snapcast...</p>
      </div>

      <!-- État "connected" - Serveur connecté -->
      <div v-else class="connected-state">
          <h2>Connecté à MacOS</h2>
          <p>{{ formattedServerName }}</p>

          <div class="actions">
              <button @click="disconnect" class="disconnect-button" :disabled="snapclientStore.isLoading">
                  Déconnecter
              </button>
          </div>
      </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted } from 'vue';
import { useSnapclientStore } from '@/stores/snapclient';
import { useAudioStore } from '@/stores/index';
import useWebSocket from '@/services/websocket';

const snapclientStore = useSnapclientStore();
const audioStore = useAudioStore();
const { on } = useWebSocket();

// Utiliser directement l'état du store pour simplifier
const isConnected = computed(() => snapclientStore.isConnected);

// Formater le nom du serveur
const formattedServerName = computed(() => {
  if (!snapclientStore.deviceName) return 'Serveur inconnu';
  const name = snapclientStore.deviceName.replace(/\.local$|\.home$/g, '');
  return name.charAt(0).toUpperCase() + name.slice(1);
});

// Se déconnecter du serveur
async function disconnect() {
  try {
      await snapclientStore.disconnectFromServer();
  } catch (err) {
      console.error('Erreur de déconnexion:', err);
  }
}

// Configurer les écouteurs d'événements WebSocket
function setupWebSocketEvents() {
  const wsUnsubscribers = [];
  
  // Événements critiques
  ['snapclient_monitor_connected', 'snapclient_monitor_disconnected', 
   'snapclient_server_disappeared', 'snapclient_server_discovered'].forEach(eventType => {
      const unsub = on(eventType, (data) => {
          snapclientStore.updateFromWebSocketEvent(eventType, data);
      });
      wsUnsubscribers.push(unsub);
  });

  // Mettre à jour l'état
  const unsubAudio = on('audio_status_updated', (data) => {
      if (data.source === 'snapclient') {
          snapclientStore.updateFromStateEvent(data);
      }
  });
  wsUnsubscribers.push(unsubAudio);
  
  // Retourner les fonctions de nettoyage
  return () => {
      wsUnsubscribers.forEach(unsub => unsub && unsub());
  };
}

onMounted(async () => {
  // Récupérer le statut initial
  await snapclientStore.fetchStatus(true);
  
  // Configurer les événements WebSocket
  const cleanupWebSocket = setupWebSocketEvents();
  
  // Cleanup lors du démontage
  onUnmounted(() => {
      cleanupWebSocket();
  });
});
</script>

<style scoped>
.snapclient-component {
max-width: 500px;
margin: 0 auto;
padding: 1rem;
}

.waiting-state, .connected-state {
text-align: center;
padding: 1.5rem;
border: 1px solid #ddd;
border-radius: 4px;
background-color: #f9f9f9;
}

.actions {
margin-top: 1rem;
}

button {
background-color: #2196F3;
color: white;
border: none;
border-radius: 4px;
padding: 8px 16px;
cursor: pointer;
transition: background-color 0.2s;
}

button:hover:not(:disabled) {
background-color: #0b7dda;
}

button:disabled {
background-color: #ccc;
cursor: not-allowed;
}

.disconnect-button {
background-color: #e74c3c;
}

.disconnect-button:hover:not(:disabled) {
background-color: #c0392b;
}
</style>