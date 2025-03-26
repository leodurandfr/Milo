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
    
    <div v-if="error" class="error-message">
      {{ error }}
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted } from 'vue';
import { useSnapclientStore } from '@/stores/snapclient';

const snapclientStore = useSnapclientStore();

// Extraire les propriétés du store
const error = computed(() => snapclientStore.error);
const isLoading = computed(() => snapclientStore.isLoading);

// Actions
async function discoverServers() {
  try {
    await snapclientStore.discoverServers();
    
    // Si des serveurs sont trouvés et non blacklistés, s'y connecter automatiquement
    const servers = snapclientStore.discoveredServers;
    const blacklisted = snapclientStore.blacklistedServers;
    
    if (servers && servers.length > 0) {
      for (const server of servers) {
        // Ne tenter la connexion qu'aux serveurs non blacklistés
        if (!blacklisted.includes(server.host)) {
          console.log(`Tentative de connexion automatique à ${server.host}`);
          try {
            await snapclientStore.connectToServer(server.host);
            return; // Sortir après première connexion réussie
          } catch (connErr) {
            console.error(`Échec de connexion à ${server.host}:`, connErr);
            // Continuer à essayer les autres serveurs
          }
        }
      }
    }
  } catch (err) {
    console.error('Erreur lors de la découverte des serveurs:', err);
  }
}

// Découvrir les serveurs au montage du composant
let discoveryInterval = null;

onMounted(async () => {
  // Récupérer le statut initial
  await snapclientStore.fetchStatus();
  
  // Découvrir les serveurs immédiatement
  await discoverServers();
  
  // Configurer un intervalle pour la découverte automatique
  discoveryInterval = setInterval(async () => {
    if (snapclientStore.pluginState === 'ready_to_connect') {
      await discoverServers();
    }
  }, 10000); // 10 secondes
});

onUnmounted(() => {
  // Nettoyer l'intervalle lors du démontage
  if (discoveryInterval) {
    clearInterval(discoveryInterval);
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