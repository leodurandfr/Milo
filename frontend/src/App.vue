<!-- frontend/src/App.vue -->
<template>
  <div class="app-container">
    <router-view />
    <VolumeBar ref="volumeBar" />
    <BottomNavigation />
    
    <!-- Modales avec header conditionnel -->
    <Modal 
      :is-open="modalStore.isSnapcastOpen" 
      :title="modalStore.currentTitle"
      :show-back-button="modalStore.canGoBack"
      @close="modalStore.closeAll"
      @back="modalStore.goBack"
    >
      <SnapcastModal />
    </Modal>
    
    <Modal 
      :is-open="modalStore.isEqualizerOpen" 
      :title="modalStore.currentTitle"
      :show-back-button="modalStore.canGoBack"
      @close="modalStore.closeAll"
      @back="modalStore.goBack"
    >
      <EqualizerModal />
    </Modal>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted } from 'vue';
import VolumeBar from '@/components/ui/VolumeBar.vue';
import BottomNavigation from '@/components/navigation/BottomNavigation.vue';
import Modal from '@/components/ui/Modal.vue';
import SnapcastModal from '@/components/snapcast/SnapcastModal.vue';
import EqualizerModal from '@/components/equalizer/EqualizerModal.vue';
import { useVolumeStore } from '@/stores/volumeStore';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useModalStore } from '@/stores/modalStore';
import useWebSocket from '@/services/websocket';

const volumeStore = useVolumeStore();
const unifiedStore = useUnifiedAudioStore();
const modalStore = useModalStore();
const { on } = useWebSocket();

// Stocker les fonctions de cleanup
const cleanupFunctions = [];

onMounted(() => {
  // === ÉVÉNEMENTS VOLUME ===
  const volumeCleanup = on('volume', 'volume_changed', (event) => {
    volumeStore.handleVolumeEvent(event);
  });
  
  // === ÉVÉNEMENTS SYSTÈME (pour tous les plugins) ===
  const systemSubscriptions = [
    on('system', 'state_changed', (event) => {
      unifiedStore.updateState(event);
    }),
    on('system', 'transition_start', (event) => {
      unifiedStore.updateState(event);
    }),
    on('system', 'transition_complete', (event) => {
      unifiedStore.updateState(event);
    }),
    on('system', 'error', (event) => {
      unifiedStore.updateState(event);
    })
  ];

  // === ÉVÉNEMENTS PLUGINS (pour tous les plugins) ===
  const pluginSubscriptions = [
    on('plugin', 'state_changed', (event) => {
      unifiedStore.updateState(event);
    }),
    on('plugin', 'metadata', (event) => {
      unifiedStore.updateState(event);
    })
  ];
  
  cleanupFunctions.push(volumeCleanup, ...systemSubscriptions, ...pluginSubscriptions);
  
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