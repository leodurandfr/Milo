<template>
    <div class="snapclient-display">
      <SnapclientWaitingConnection v-if="!isConnected" />
      <SnapclientConnectionInfo v-else />
    </div>
  </template>
  
  <script setup>
  import { computed, onMounted, onUnmounted, watch } from 'vue';
  import { useAudioStore } from '@/stores/index';
  import { useSnapclientStore } from '@/stores/snapclient';
  import SnapclientWaitingConnection from './SnapclientWaitingConnection.vue';
  import SnapclientConnectionInfo from './SnapclientConnectionInfo.vue';
  
  const audioStore = useAudioStore();
  const snapclientStore = useSnapclientStore();
  
  // État dérivé pour contrôler l'affichage
  const isConnected = computed(() => 
    snapclientStore.isConnected && 
    snapclientStore.pluginState === 'connected'
  );
  
  // Surveiller les changements d'état audio
  watch(() => audioStore.currentState, async (newState, oldState) => {
    if (newState === 'macos' && oldState !== 'macos') {
      // Activation de la source MacOS
      await snapclientStore.fetchStatus();
    } else if (oldState === 'macos' && newState !== 'macos') {
      // Désactivation de la source MacOS
      snapclientStore.reset();
    }
  });
  
  // Vérification périodique du statut
  let statusInterval = null;
  
  onMounted(async () => {
    await snapclientStore.fetchStatus();
    
    // Un seul intervalle pour la vérification du statut
    statusInterval = setInterval(async () => {
      if (audioStore.currentState === 'macos') {
        try {
          await snapclientStore.fetchStatus();
          
          // Détection d'état incohérent sans rechargement de page
          if (!snapclientStore.isConnected && isConnected.value) {
            // Force la réaction des computed properties
            snapclientStore.$patch({isConnected: false});
          }
        } catch (err) {
          console.error('Erreur lors de la vérification du statut:', err);
        }
      }
    }, 3000);
  });
  
  onUnmounted(() => {
    if (statusInterval) {
      clearInterval(statusInterval);
    }
  });
  </script>
  
  <style scoped>
  .snapclient-display {
    width: 100%;
    padding: 1rem;
  }
  </style>