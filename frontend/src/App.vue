<!-- App.vue - Version with i18n WebSocket -->
<template>
  <div class="app-container">
    <!-- App content only renders after boot completes -->
    <template v-if="isBootComplete">
      <router-view />
      <VolumeBar />
      <Dock
        @open-multiroom="isMultiroomOpen = true"
        @open-settings="isSettingsOpen = true"
      />
    </template>

    <Modal :is-open="isMultiroomOpen" @close="isMultiroomOpen = false">
      <MultiroomModal />
    </Modal>

    <Modal :is-open="isSettingsOpen" @close="closeSettings">
      <SettingsModal :initial-view="settingsInitialView" />
    </Modal>

    <!-- Global Virtual Keyboard -->
    <VirtualKeyboard />

  </div>
</template>

<script setup>
import { ref, watch, onMounted, onUnmounted, provide, defineAsyncComponent } from 'vue';
import VolumeBar from '@/components/ui/VolumeBar.vue';
import Dock from '@/components/ui/Dock.vue';
import Modal from '@/components/ui/Modal.vue';

// Lazy-loaded modals
const MultiroomModal = defineAsyncComponent(() =>
  import('@/components/multiroom/MultiroomModal.vue')
);
const SettingsModal = defineAsyncComponent(() =>
  import('@/components/settings/SettingsModal.vue')
);
const VirtualKeyboard = defineAsyncComponent(() =>
  import('@/components/ui/VirtualKeyboard.vue')
);

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

// Track if initial state received from WebSocket (hides boot screen)
const isReady = ref(false);
const isBootComplete = ref(false);

// Fade out boot screen after WebSocket connects + delay
watch(isReady, (ready) => {
  if (ready) {
    const bootScreen = document.getElementById('boot-screen');
    if (bootScreen) {
      // Start logo animation 0.1s before boot-screen ends
      setTimeout(() => {
        bootScreen.classList.add('logo-exit');
      }, 1400);

      // Fade out boot-screen and mount app
      setTimeout(() => {
        bootScreen.classList.add('fade-out');
        isBootComplete.value = true;
        setTimeout(() => bootScreen.remove(), 400);
      }, 1500);
    }
  }
});

const isMultiroomOpen = ref(false);
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
provide('openMultiroom', () => isMultiroomOpen.value = true);
provide('openSettings', openSettings);
provide('closeModals', () => {
  isMultiroomOpen.value = false;
  closeSettings();
});

const cleanupFunctions = [];

onMounted(async () => {
  // Register WebSocket event listeners FIRST (before any async operations)
  // This prevents race condition where initial_state arrives before listeners are ready
  cleanupFunctions.push(
    on('system', 'initial_state', (event) => {
      unifiedStore.updateState(event);

      // Populate podcastStore if active source is podcast
      const fullState = event.data?.full_state;
      if (fullState?.active_source === 'podcast' && fullState?.metadata) {
        podcastStore.handleStateUpdate(fullState.metadata);
      }

      isReady.value = true;
    }),
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

  // Preload podcast subscriptions list in background (for instant hasSubscriptions check)
  // Only fetches local data, no Taddy API call - episodes loaded when HomeView opens
  podcastStore.preloadSubscriptionsList()

  // Preload modals in background for instant display when user opens them
  Promise.all([
    import('@/components/multiroom/MultiroomModal.vue'),
    import('@/components/settings/SettingsModal.vue'),
    import('@/components/ui/VirtualKeyboard.vue')
  ]);
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