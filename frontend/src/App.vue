<!-- App.vue - Version with i18n WebSocket -->
<template>
  <div class="app-container">
    <router-view />
    <VolumeBar ref="volumeBar" />
    <BottomNavigation
      @open-snapcast="isSnapcastOpen = true"
      @open-equalizer="isEqualizerOpen = true"
      @open-settings="isSettingsOpen = true"

    />

    <Modal :is-open="isSnapcastOpen" @close="isSnapcastOpen = false">
      <SnapcastModal />
    </Modal>

    <Modal :is-open="isEqualizerOpen" @close="isEqualizerOpen = false">
      <EqualizerModal />
    </Modal>

    <Modal :is-open="isSettingsOpen" @close="closeSettings">
      <SettingsModal :initial-view="settingsInitialView" />
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
import { usePodcastStore } from '@/stores/podcastStore';
import { useSettingsStore } from '@/stores/settingsStore';
import { i18n } from '@/services/i18n';
import useWebSocket from '@/services/websocket';
import { useScreenActivity } from '@/composables/useScreenActivity';
import { useHardwareConfig } from '@/composables/useHardwareConfig';

const unifiedStore = useUnifiedAudioStore();
const podcastStore = usePodcastStore();
const settingsStore = useSettingsStore();
const { on, onReconnect } = useWebSocket();
const { loadHardwareInfo } = useHardwareConfig();

// Enable screen activity detection (touch, mouse, keyboard)
useScreenActivity();

const volumeBar = ref(null);
const isSnapcastOpen = ref(false);
const isEqualizerOpen = ref(false);
const isSettingsOpen = ref(false);

// Settings navigation - supports direct navigation to sub-views
const settingsInitialView = ref('home');

function openSettings(initialView = 'home') {
  settingsInitialView.value = initialView;
  isSettingsOpen.value = true;
}

function closeSettings() {
  isSettingsOpen.value = false;
  settingsInitialView.value = 'home'; // Reset for next open
}

// Provide for child components
provide('openSnapcast', () => isSnapcastOpen.value = true);
provide('openEqualizer', () => isEqualizerOpen.value = true);
provide('openSettings', openSettings);
provide('closeModals', () => {
  isSnapcastOpen.value = false;
  isEqualizerOpen.value = false;
  closeSettings();
});

const cleanupFunctions = [];

onMounted(async () => {
  // Register WebSocket event listeners FIRST (before any async operations)
  // This prevents race condition where initial_state arrives before listeners are ready
  cleanupFunctions.push(
    on('system', 'initial_state', (event) => unifiedStore.updateState(event)),
    on('volume', 'volume_changed', (event) => unifiedStore.handleVolumeEvent(event)),
    on('system', 'state_changed', (event) => unifiedStore.updateState(event)),
    on('system', 'transition_start', (event) => unifiedStore.updateState(event)),
    on('system', 'transition_complete', (event) => unifiedStore.updateState(event)),
    on('system', 'error', (event) => unifiedStore.updateState(event)),
    on('plugin', 'state_changed', (event) => {
      unifiedStore.updateState(event);
      podcastStore.handlePluginEvent(event);
    }),
    on('plugin', 'metadata', (event) => {
      unifiedStore.updateState(event);
      podcastStore.handlePluginEvent(event);
    }),
    on('settings', 'language_changed', (event) => {
      if (event.data?.language) {
        i18n.handleLanguageChanged(event.data.language);
      }
    }),
    onReconnect(() => {
      console.log('WebSocket reconnected');
    })
  );

  // Setup visibility listener (synchronous)
  const visibilityCleanup = unifiedStore.setupVisibilityListener();
  cleanupFunctions.push(visibilityCleanup);

  // Now perform async initialization
  await loadHardwareInfo();
  await settingsStore.loadAllSettings();

  // Final synchronous setup
  unifiedStore.setVolumeBarRef(volumeBar);
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