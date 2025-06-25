<!-- frontend/src/App.vue -->
<template>
  <div class="app-container">
    <router-view />
    <VolumeBar ref="volumeBar" />
    <BottomNavigation />
    
    <!-- Modales avec système de navigation -->
    <Modal 
      :is-open="modalStore.isSnapcastOpen" 
      :title="modalStore.currentTitle" 
      size="large"
      :show-back-button="modalStore.canGoBack"
      @close="modalStore.closeAll"
      @back="modalStore.goBack"
    >
      <SnapcastModal />
    </Modal>
    
    <Modal 
      :is-open="modalStore.isEqualizerOpen" 
      :title="modalStore.currentTitle" 
      size="medium"
      :show-back-button="modalStore.canGoBack"
      @close="modalStore.closeAll"
      @back="modalStore.goBack"
    >
      <EqualizerModal />
    </Modal>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, watch } from 'vue';
import { useRouter } from 'vue-router';
import VolumeBar from '@/components/ui/VolumeBar.vue';
import BottomNavigation from '@/components/navigation/BottomNavigation.vue';
import Modal from '@/components/ui/Modal.vue';
import SnapcastModal from '@/components/snapcast/SnapcastModal.vue';
import EqualizerModal from '@/components/equalizer/EqualizerModal.vue';
import { useVolumeStore } from '@/stores/volumeStore';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useModalStore } from '@/stores/modalStore';
import useWebSocket from '@/services/websocket';

const router = useRouter();
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

// === SYNCHRONISATION AUTOMATIQUE DES ROUTES ===
// Quand le plugin actif change via WebSocket, synchroniser la route sur tous les devices
watch(() => unifiedStore.currentSource, (newSource, oldSource) => {
  // Mapping des sources vers les routes
  const routeMap = {
    'librespot': '/librespot',
    'bluetooth': '/bluetooth', 
    'roc': '/roc'
  };
  
  const targetRoute = routeMap[newSource];
  
  // Naviguer automatiquement si :
  // 1. La nouvelle source a une route définie
  // 2. On n'est pas déjà sur cette route
  // 3. Le changement vient d'un autre device (éviter double navigation)
  if (targetRoute && router.currentRoute.value.path !== targetRoute) {
    console.log(`🔄 Auto-navigation: ${oldSource} → ${newSource} (route: ${targetRoute})`);
    router.push(targetRoute);
  }
}, { immediate: false }); // immediate: false pour éviter la navigation au premier chargement

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