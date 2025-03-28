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
import { computed, onMounted, onUnmounted, watch } from 'vue';
import { useSnapclientStore } from '@/stores/snapclient';
import { useAudioStore } from '@/stores/index';

const snapclientStore = useSnapclientStore();
const audioStore = useAudioStore();

// Extraire les propriétés du store
const error = computed(() => snapclientStore.error);
const isLoading = computed(() => snapclientStore.isLoading);
const isActive = computed(() => snapclientStore.isActive);

// CORRECTION: Observer l'état audio pour arrêter la découverte si on change de source
watch(() => audioStore.currentState, (newState) => {
  if (newState !== 'macos') {
    // Arrêter la découverte si on change de source
    if (discoveryInterval) {
      clearInterval(discoveryInterval);
      discoveryInterval = null;
    }
  }
});

// Actions
async function discoverServers() {
  // CORRECTION: Vérifier si le plugin est toujours actif
  if (!isActive.value) {
    console.log('Découverte ignorée - plugin est inactif');
    return;
  }

  try {
    const result = await snapclientStore.discoverServers();
    
    // Si le plugin est devenu inactif, ne rien faire
    if (result && result.inactive === true) {
      console.log('Résultat de découverte ignoré - plugin devenu inactif');
      return;
    }
    
    // Code existant pour la connexion automatique
    // ...
  } catch (err) {
    console.error('Erreur lors de la découverte des serveurs:', err);
  }
}

// Découvrir les serveurs au montage du composant
let discoveryInterval = null;

onMounted(async () => {
  // Récupérer le statut initial
  await snapclientStore.fetchStatus();
  
  // CORRECTION: Vérifier si le plugin est actif avant de démarrer
  if (!isActive.value) {
    console.log('Composant monté mais plugin inactif, pas de découverte');
    return;
  }
  
  // Découvrir les serveurs immédiatement
  await discoverServers();
  
  // Configurer un intervalle pour la découverte automatique
  discoveryInterval = setInterval(async () => {
    // CORRECTION: Vérifier l'état actif à chaque itération
    if (!isActive.value || audioStore.currentState !== 'macos') {
      clearInterval(discoveryInterval);
      discoveryInterval = null;
      return;
    }
    
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