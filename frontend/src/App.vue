<!-- App.vue - Version avec i18n WebSocket -->
<template>
  <div class="app-container">
    <router-view />
    <VolumeBar ref="volumeBar" />
    <BottomNavigation
      @open-snapcast="isSnapcastOpen = true"
      @open-equalizer="isEqualizerOpen = true"
      @open-settings="isSettingsOpen = true"

    />

    <Modal :is-open="isSnapcastOpen" height-mode="auto" @close="isSnapcastOpen = false">
      <SnapcastModal />
    </Modal>

    <Modal :is-open="isEqualizerOpen" height-mode="fixed" @close="isEqualizerOpen = false">
      <EqualizerModal />
    </Modal>

    <Modal :is-open="isSettingsOpen" height-mode="fixed" @close="isSettingsOpen = false">
      <SettingsModal />
    </Modal>

    <!-- Global Virtual Keyboard -->
    <VirtualKeyboard />

  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, provide } from 'vue';
import VolumeBar from '@/components/ui/VolumeBar.vue';
import BottomNavigation from '@/components/navigation/BottomNavigation.vue';
import Modal from '@/components/ui/Modal.vue';
import SnapcastModal from '@/components/snapcast/SnapcastModal.vue';
import EqualizerModal from '@/components/equalizer/EqualizerModal.vue';
import SettingsModal from '@/components/settings/SettingsModal.vue';
import VirtualKeyboard from '@/components/ui/VirtualKeyboard.vue';

import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { i18n } from '@/services/i18n';
import useWebSocket from '@/services/websocket';
import { useScreenActivity } from '@/composables/useScreenActivity';
import { useHardwareConfig } from '@/composables/useHardwareConfig';

const unifiedStore = useUnifiedAudioStore();
const { on, onReconnect } = useWebSocket();
const { loadHardwareInfo } = useHardwareConfig();

// Activer la détection d'activité écran (touch, souris, clavier)
useScreenActivity();

const volumeBar = ref(null);
const isSnapcastOpen = ref(false);
const isEqualizerOpen = ref(false);
const isSettingsOpen = ref(false);

// Provide pour les composants enfants
provide('openSnapcast', () => isSnapcastOpen.value = true);
provide('openEqualizer', () => isEqualizerOpen.value = true);
provide('openSettings', () => isSettingsOpen.value = true);
provide('closeModals', () => {
  isSnapcastOpen.value = false;
  isEqualizerOpen.value = false;
  isSettingsOpen.value = false;

});

const cleanupFunctions = [];

onMounted(async () => {
  // Charger les infos hardware AVANT tout le reste (critique pour VirtualKeyboard)
  await loadHardwareInfo();

  // Configuration initiale
  unifiedStore.setVolumeBarRef(volumeBar);

  // Setup des listeners
  const visibilityCleanup = unifiedStore.setupVisibilityListener();
  cleanupFunctions.push(visibilityCleanup);
  
  // Événements WebSocket - Audio
  cleanupFunctions.push(
    on('volume', 'volume_changed', (event) => unifiedStore.handleVolumeEvent(event)),
    on('system', 'state_changed', (event) => unifiedStore.updateState(event)),
    on('system', 'transition_start', (event) => unifiedStore.updateState(event)),
    on('system', 'transition_complete', (event) => unifiedStore.updateState(event)),
    on('system', 'error', (event) => unifiedStore.updateState(event)),
    on('plugin', 'state_changed', (event) => unifiedStore.updateState(event)),
    on('plugin', 'metadata', (event) => unifiedStore.updateState(event))
  );

  // Événements WebSocket - i18n
  cleanupFunctions.push(
    on('settings', 'language_changed', (event) => {
      console.log('Received language_changed WebSocket event:', event);
      if (event.data?.language) {
        console.log(`Changing language to: ${event.data.language}`);
        i18n.handleLanguageChanged(event.data.language);
      } else {
        console.error('Language_changed event missing language data:', event);
      }
    })
  );

  // Reconnexion WebSocket
  cleanupFunctions.push(
    onReconnect(() => {
      console.log('WebSocket reconnected - full state sync incoming');
    })
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