<!-- App.vue - Phase 2 : Store unifié simplifié -->
<template>
  <div class="app-container">
    <router-view />
    <VolumeBar ref="volumeBar" />
    <BottomNavigation 
      @open-snapcast="isSnapcastOpen = true"
      @open-equalizer="isEqualizerOpen = true"
    />

    <Modal :is-open="isSnapcastOpen" height-mode="auto" @close="isSnapcastOpen = false">
      <SnapcastModal />
    </Modal>

    <Modal :is-open="isEqualizerOpen" height-mode="fixed" @close="isEqualizerOpen = false">
      <EqualizerModal />
    </Modal>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, provide } from 'vue';
import VolumeBar from '@/components/ui/VolumeBar.vue';
import BottomNavigation from '@/components/navigation/BottomNavigation.vue';
import Modal from '@/components/ui/Modal.vue';
import SnapcastModal from '@/components/snapcast/SnapcastModal.vue';
import EqualizerModal from '@/components/equalizer/EqualizerModal.vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import useWebSocket from '@/services/websocket';

// SIMPLIFIÉ : Un seul store
const unifiedStore = useUnifiedAudioStore();
const { on } = useWebSocket();

const volumeBar = ref(null);

// Modales
const isSnapcastOpen = ref(false);
const isEqualizerOpen = ref(false);

// Provide pour les composants enfants
provide('openSnapcast', () => isSnapcastOpen.value = true);
provide('openEqualizer', () => isEqualizerOpen.value = true);
provide('closeModals', () => {
  isSnapcastOpen.value = false;
  isEqualizerOpen.value = false;
});

const cleanupFunctions = [];

onMounted(() => {
  // Configurer la référence VolumeBar dans le store
  unifiedStore.setVolumeBarRef(volumeBar);
  
  // Setup listeners
  unifiedStore.setupVisibilityListener();
  
  // Événements WebSocket SIMPLIFIÉS
  cleanupFunctions.push(
    // Volume (via store unifié)
    on('volume', 'volume_changed', (event) => unifiedStore.handleVolumeEvent(event)),
    
    // Système (via store unifié)
    on('system', 'state_changed', (event) => unifiedStore.updateState(event)),
    on('system', 'transition_start', (event) => unifiedStore.updateState(event)),
    on('system', 'transition_complete', (event) => unifiedStore.updateState(event)),
    on('system', 'error', (event) => unifiedStore.updateState(event)),
    
    // Plugins (via store unifié)
    on('plugin', 'state_changed', (event) => unifiedStore.updateState(event)),
    on('plugin', 'metadata', (event) => unifiedStore.updateState(event))
  );

  // État initial SIMPLIFIÉ
  unifiedStore.refreshState();
});

onUnmounted(() => {
  unifiedStore.removeVisibilityListener();
  cleanupFunctions.forEach(cleanup => cleanup());
});
</script>

<style>
.app-container {
  height: 100%;
}
</style>