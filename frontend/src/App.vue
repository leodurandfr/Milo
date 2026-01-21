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

    <!-- Connection status indicator (AC2: UI indicates connection status) -->
    <Transition name="slide-up">
      <div v-if="isBootComplete && !isConnected" class="connection-status">
        {{ $t('app.connectionLost') }}
      </div>
    </Transition>

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
import { useClientRegistryStore } from '@/stores/clientRegistryStore';
import { useDspStore } from '@/stores/dspStore';
import { i18n } from '@/services/i18n';
import useWebSocket from '@/services/websocket';
import { useScreenActivity } from '@/composables/useScreenActivity';
import { useHardwareConfig } from '@/composables/useHardwareConfig';

const unifiedStore = useUnifiedAudioStore();
const podcastStore = usePodcastStore();
const settingsStore = useSettingsStore();
const clientRegistryStore = useClientRegistryStore();
const dspStore = useDspStore();
const { on, onReconnect, isConnected } = useWebSocket();
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
      }, 700);

      // Fade out boot-screen and mount app
      setTimeout(() => {
        bootScreen.classList.add('fade-out');
        isBootComplete.value = true;
        setTimeout(() => bootScreen.remove(), 400);
      }, 800);
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
    // Multiroom events - new standardized format (Story 6.2)
    on('multiroom', 'client_state_changed', (event) => clientRegistryStore.handleMultiroomEvent(event)),
    on('multiroom', 'zone_changed', (event) => clientRegistryStore.handleMultiroomEvent(event)),
    on('multiroom', 'dsp_changed', (event) => dspStore.handleDspChanged(event)),
    on('multiroom', 'crossover_changed', (event) => dspStore.handleZoneCrossoverChanged(event)),
    onReconnect(() => {
      console.log('WebSocket reconnected');
      // Refresh registry state on reconnect (AC3: State Resync)
      clientRegistryStore.fetchState();
      // Refresh DSP state for current target
      dspStore.loadStatus();
    })
  );

  // Setup visibility listener (synchronous)
  const visibilityCleanup = unifiedStore.setupVisibilityListener();
  cleanupFunctions.push(visibilityCleanup);

  // Now perform async initialization
  await loadHardwareInfo();
  await settingsStore.loadAllSettings();

  // Initialize client registry (loads from cache + fetches fresh state)
  clientRegistryStore.initialize();

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

/* Connection status indicator */
.connection-status {
  position: fixed;
  bottom: 80px;
  left: 50%;
  transform: translateX(-50%);
  background-color: rgba(220, 53, 69, 0.95);
  color: white;
  padding: 8px 16px;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 500;
  z-index: 9999;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.3);
}

/* Slide-up transition */
.slide-up-enter-active,
.slide-up-leave-active {
  transition: all 0.3s ease;
}

.slide-up-enter-from,
.slide-up-leave-to {
  opacity: 0;
  transform: translateX(-50%) translateY(20px);
}
</style>