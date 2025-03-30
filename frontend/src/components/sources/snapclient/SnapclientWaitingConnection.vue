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
    
    <div v-if="discoveredServers.length > 0" class="servers-list">
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
import { computed, onMounted, onUnmounted, ref } from 'vue';
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

// Références pour les fonctions de désabonnement
let unsubscribeServerFound = null;

// Actions
async function discoverServers() {
  console.log("🔍 Recherche manuelle de serveurs lancée");
  
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
  } catch (err) {
    console.error('❌ Erreur lors de la découverte des serveurs:', err);
  }
}

async function connectToServer(serverHost) {
  console.log(`🔌 Tentative de connexion à ${serverHost}`);
  
  try {
    await snapclientStore.connectToServer(serverHost);
  } catch (err) {
    console.error(`❌ Erreur lors de la connexion à ${serverHost}:`, err);
  }
}

onMounted(async () => {
  // Récupérer le statut initial
  await snapclientStore.fetchStatus();
  
  // Si aucun serveur n'est trouvé, lancer une découverte initiale
  if (discoveredServers.value.length === 0) {
    console.log("🔍 Aucun serveur dans l'état initial, lancement de la découverte");
    await discoverServers();
  }
  
  // Écouter les événements de découverte de serveurs via WebSocket
  unsubscribeServerFound = on('snapclient_server_event', (data) => {
    console.log("⚡ Événement serveur reçu dans WaitingConnection");
    
    // Si on reçoit un événement qui indique de nouveaux serveurs, actualiser la liste
    if (data.method === "Server.GetStatus" || data.method === "Server.OnUpdate") {
      console.log("🔄 Mise à jour de la liste des serveurs suite à l'événement");
      snapclientStore.fetchStatus();
    }
  });
});

onUnmounted(() => {
  // Nettoyer les abonnements aux événements
  if (unsubscribeServerFound) {
    unsubscribeServerFound();
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
</style>