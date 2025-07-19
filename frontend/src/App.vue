<!-- App.vue - Version finale OPTIM -->
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

const unifiedStore = useUnifiedAudioStore();
const { on } = useWebSocket();

const volumeBar = ref(null);
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
  // Configuration initiale
  unifiedStore.setVolumeBarRef(volumeBar);
  
  // Setup des listeners
  const visibilityCleanup = unifiedStore.setupVisibilityListener();
  cleanupFunctions.push(visibilityCleanup);
  
  // Événements WebSocket
  cleanupFunctions.push(
    on('volume', 'volume_changed', (event) => unifiedStore.handleVolumeEvent(event)),
    on('system', 'state_changed', (event) => unifiedStore.updateState(event)),
    on('system', 'transition_start', (event) => unifiedStore.updateState(event)),
    on('system', 'transition_complete', (event) => unifiedStore.updateState(event)),
    on('system', 'error', (event) => unifiedStore.updateState(event)),
    on('plugin', 'state_changed', (event) => unifiedStore.updateState(event)),
    on('plugin', 'metadata', (event) => unifiedStore.updateState(event))
  );

  // État initial
  unifiedStore.refreshState();
});

onUnmounted(() => {
  cleanupFunctions.forEach(cleanup => cleanup());
});
</script>

<style>
.app-container {
  height: 100%;
}
</style>