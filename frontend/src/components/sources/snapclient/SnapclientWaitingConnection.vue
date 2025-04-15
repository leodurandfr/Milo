<template>
  <div class="waiting-connection">
    <h2>En attente de connexion MacOS</h2>
    <p>Attendez qu'un Mac se connecte via Snapcast...</p>
    
    <div class="discovery-section">
      <button @click="discoverAndConnect" :disabled="isDiscovering">
        {{ isDiscovering ? 'Recherche en cours...' : 'Rechercher des serveurs' }}
      </button>
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

const snapclientStore = useSnapclientStore();
const audioStore = useAudioStore();

// État local simplifié
const isDiscovering = ref(false);
const reconnectTimer = ref(null);

// Propriétés du store
const error = computed(() => snapclientStore.error);

// Fonction pour découvrir et se connecter automatiquement
async function discoverAndConnect() {
  if (isDiscovering.value) return;
  
  try {
    isDiscovering.value = true;
    
    // Découvrir les serveurs
    const result = await snapclientStore.discoverServers();
    
    if (!result.servers || result.servers.length === 0) {
      console.log("Aucun serveur trouvé");
      return;
    }
    
    // Tenter de se connecter au dernier serveur connu
    const lastServer = localStorage.getItem('lastSnapclientServer');
    if (lastServer) {
      try {
        const serverData = JSON.parse(lastServer);
        // Vérifier si le dernier serveur est toujours disponible
        const foundServer = result.servers.find(s => s.host === serverData.host);
        
        if (foundServer) {
          console.log("Reconnexion au dernier serveur:", serverData.host);
          await snapclientStore.connectToServer(serverData.host);
          return;
        }
      } catch (e) {
        console.error("Erreur de parsing du dernier serveur:", e);
      }
    }
    
    // Si un seul serveur est disponible, s'y connecter automatiquement
    if (result.servers.length === 1) {
      const server = result.servers[0];
      console.log("Un seul serveur disponible, connexion automatique:", server.name);
      await snapclientStore.connectToServer(server.host);
    }
    
  } catch (err) {
    console.error("Erreur lors de la découverte/connexion:", err);
  } finally {
    isDiscovering.value = false;
  }
}

onMounted(() => {
  // Lancer une découverte initiale
  discoverAndConnect();
  
  // Configurer une détection périodique tant qu'on n'est pas connecté
  reconnectTimer.value = setInterval(() => {
    if (audioStore.currentState === 'macos' && !snapclientStore.isConnected) {
      discoverAndConnect();
    }
  }, 10000); // Toutes les 10 secondes
});

onUnmounted(() => {
  // Nettoyer l'intervalle
  if (reconnectTimer.value) {
    clearInterval(reconnectTimer.value);
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

.discovery-section {
  margin: 1rem 0;
}

.error-message {
  background-color: #e74c3c;
  color: white;
  padding: 10px;
  margin: 10px 0;
}

button {
  background-color: #2196F3;
  color: white;
  border: none;
  border-radius: 4px;
  padding: 8px 16px;
  cursor: pointer;
}

button:hover:not(:disabled) {
  background-color: #0b7dda;
}

button:disabled {
  background-color: #ccc;
  cursor: not-allowed;
}
</style>