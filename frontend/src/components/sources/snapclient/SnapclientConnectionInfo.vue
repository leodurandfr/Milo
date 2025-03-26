<template>
    <div class="snapclient-connection-info">
      <!-- État connecté -->
      <div class="connection-state">
        <h2>Connecté à MacOS</h2>
        <p>{{ formattedServerName }}</p>
        
        <div class="actions">
          <button @click="disconnectFromServer" class="disconnect-button" :disabled="isLoading">
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
  import { computed, onMounted } from 'vue';
  import { useSnapclientStore } from '@/stores/snapclient';
  
  const snapclientStore = useSnapclientStore();
  
  // Extraire les propriétés du store
  const isConnected = computed(() => snapclientStore.isConnected);
  const deviceName = computed(() => snapclientStore.deviceName);
  const host = computed(() => snapclientStore.host);
  const error = computed(() => snapclientStore.error);
  const isLoading = computed(() => snapclientStore.isLoading);
  
  // Formater le nom du serveur
  const formattedServerName = computed(() => {
    // Si pas de deviceName, utiliser une valeur par défaut
    if (!deviceName.value) return 'Serveur inconnu';
    
    // Nettoyer le nom du serveur (enlever .local, etc.)
    let name = deviceName.value;
    name = name.replace('.local', '').replace('.home', '');
    
    // Mettre la première lettre en majuscule
    name = name.charAt(0).toUpperCase() + name.slice(1);
    
    return name;
  });
  
  // Actions
  async function disconnectFromServer() {
    try {
      await snapclientStore.disconnectFromServer();
    } catch (err) {
      console.error('Erreur lors de la déconnexion du serveur:', err);
    }
  }
  
  // Récupérer le statut initial
  onMounted(async () => {
    await snapclientStore.fetchStatus();
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
  }
  </style>