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
import { computed } from 'vue';
import { useSnapclientStore } from '@/stores/snapclient';

const snapclientStore = useSnapclientStore();

// Propriétés simplifiées
const deviceName = computed(() => snapclientStore.deviceName);
const error = computed(() => snapclientStore.error);
const isLoading = computed(() => snapclientStore.isLoading);

// Formater le nom du serveur - simplification
const formattedServerName = computed(() => {
  if (!deviceName.value) return 'Serveur inconnu';
  
  // Nettoyer et formater le nom
  const name = deviceName.value
    .replace(/\.local$|\.home$/g, '');
  
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