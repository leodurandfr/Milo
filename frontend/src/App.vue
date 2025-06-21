<!-- frontend/src/App.vue - Version corrigée avec récupération statut complet -->
<template>
  <div class="app-container">
    <router-view />
    <VolumeBar ref="volumeBar" />
  </div>
</template>

<script setup>
import { onMounted } from 'vue';
import VolumeBar from '@/components/ui/VolumeBar.vue';
import { useVolumeStore } from '@/stores/volumeStore';
import useWebSocket from '@/services/websocket';

const volumeStore = useVolumeStore();
const { on } = useWebSocket();

onMounted(() => {
  // Écouter les événements volume via WebSocket
  on('volume', 'volume_changed', (event) => {
    volumeStore.handleVolumeEvent(event);
  });
  
  // ✅ MODIFIÉ : Récupérer le statut complet (volume + limites) au démarrage
  volumeStore.getVolumeStatus();
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