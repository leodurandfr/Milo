<!-- frontend/src/App.vue - Version complète OPTIM avec refs directes -->
<template>
  <div class="app-container">
    <router-view />
    <VolumeBar ref="volumeBar" />
    <BottomNavigation 
      @open-snapcast="isSnapcastOpen = true"
      @open-equalizer="isEqualizerOpen = true"
    />

    <!-- Modal Multiroom (avec navigation interne) -->
    <Modal 
      :is-open="isSnapcastOpen" 
      height-mode="auto" 
      @close="isSnapcastOpen = false"
    >
      <SnapcastModal />
    </Modal>

    <!-- Modal Égaliseur (simple, 1 vue) -->
    <Modal 
      :is-open="isEqualizerOpen" 
      height-mode="fixed" 
      @close="isEqualizerOpen = false"
    >
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
import { useVolumeStore } from '@/stores/volumeStore';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import useWebSocket from '@/services/websocket';

const volumeStore = useVolumeStore();
const unifiedStore = useUnifiedAudioStore();
const { on } = useWebSocket();

// === MODALES PRINCIPALES : 2 REFS SIMPLES ===
const isSnapcastOpen = ref(false);
const isEqualizerOpen = ref(false);

// === PROVIDE POUR LES COMPOSANTS ENFANTS ===
provide('openSnapcast', () => isSnapcastOpen.value = true);
provide('openEqualizer', () => isEqualizerOpen.value = true);
provide('closeModals', () => {
  isSnapcastOpen.value = false;
  isEqualizerOpen.value = false;
});

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
  height: 100%;
}
</style>