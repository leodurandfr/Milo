<!-- frontend/src/App.vue - Version corrigée avec cleanup WebSocket -->
<template>
  <div class="app-container">
    <router-view />
    <VolumeBar ref="volumeBar" />
  </div>
</template>

<script setup>
import { onMounted, onUnmounted } from 'vue';
import VolumeBar from '@/components/ui/VolumeBar.vue';
import { useVolumeStore } from '@/stores/volumeStore';
import useWebSocket from '@/services/websocket';

const volumeStore = useVolumeStore();
const { on } = useWebSocket();

// Stocker les fonctions de cleanup
const cleanupFunctions = [];

onMounted(() => {
  // Écouter les événements volume via WebSocket avec cleanup
  const volumeCleanup = on('volume', 'volume_changed', (event) => {
    volumeStore.handleVolumeEvent(event);
  });
  
  cleanupFunctions.push(volumeCleanup);
  
  // Récupérer le statut complet (volume + limites) au démarrage
  volumeStore.getVolumeStatus();
});

onUnmounted(() => {
  // Nettoyer tous les event listeners WebSocket
  cleanupFunctions.forEach(cleanup => cleanup());
});
</script>

<style>
.app-container {
  width: 100%;
  height: 100vh;
  background-color: #f5f5f5;
  display: flex;
  flex-direction: column;
}
</style>