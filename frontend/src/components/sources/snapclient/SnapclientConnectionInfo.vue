<template>
  <div class="snapclient-connection-info">
    <div class="connection-state">
      <h2>Connecté à MacOS</h2>
      <p>{{ formattedServerName }}</p>
      
      <div class="actions">
        <button @click="disconnect" class="disconnect-button" :disabled="isLoading">
          Déconnecter
        </button>
      </div>
    </div>
    
    <div v-if="error" class="error-message">
      {{ error }}
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, watch } from 'vue';
import { useSnapclientStore } from '@/stores/snapclient';

const snapclientStore = useSnapclientStore();

// Propriétés calculées
const deviceName = computed(() => snapclientStore.deviceName);
const error = computed(() => snapclientStore.error);
const isLoading = computed(() => snapclientStore.isLoading);
const isConnected = computed(() => snapclientStore.isConnected);

// Observer les changements d'état de connexion
watch(isConnected, (connected) => {
  if (!connected) {
    console.log("🔌 Déconnexion détectée dans SnapclientConnectionInfo, rafraîchissement forcé");
    // Forcer l'actualisation du parent
    window.dispatchEvent(new Event('snapclient-refresh-needed'));
  }
});

// Formater le nom du serveur
const formattedServerName = computed(() => {
  if (!deviceName.value) return 'Serveur inconnu';
  
  // Nettoyer et formater le nom
  const name = deviceName.value
    .replace('.local', '')
    .replace('.home', '');
  
  return name.charAt(0).toUpperCase() + name.slice(1);
});

// Action de déconnexion simplifiée
async function disconnect() {
  try {
    await snapclientStore.disconnectFromServer();
  } catch (err) {
    console.error('Erreur de déconnexion:', err);
  }
}

// Écouteur de déconnexion global
function handleDisconnect(event) {
  console.log("📢 Événement de déconnexion global reçu dans SnapclientConnectionInfo");
  // Forcer un rendu si nécessaire
}

onMounted(() => {
  document.addEventListener('snapclient-disconnected', handleDisconnect);
});

onUnmounted(() => {
  document.removeEventListener('snapclient-disconnected', handleDisconnect);
});
</script>


<style scoped>
.snapclient-connection-info {
  max-width: 500px;
  margin: 0 auto;
  padding: 1rem;
  text-align: center;
}

.connection-state {
  border: 1px solid #ddd;
  padding: 1rem;
  border-radius: 4px;
  background-color: #f9f9f9;
}

.actions {
  margin-top: 1rem;
}

.disconnect-button {
  background-color: #e74c3c;
  color: white;
  border: none;
  border-radius: 4px;
  padding: 8px 16px;
  cursor: pointer;
  transition: background-color 0.2s;
}

.disconnect-button:hover:not(:disabled) {
  background-color: #c0392b;
}

.disconnect-button:disabled {
  background-color: #bdc3c7;
  cursor: not-allowed;
}

.error-message {
  background-color: #e74c3c;
  color: white;
  padding: 0.5rem;
  margin-top: 1rem;
  border-radius: 4px;
}
</style>