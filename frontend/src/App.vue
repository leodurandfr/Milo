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
import { useMultiroomStore } from '@/stores/multiroomStore';
import { useDspStore } from '@/stores/dspStore';
import { i18n, useI18n } from '@/services/i18n';
import useWebSocket from '@/services/websocket';
import { useScreenActivity } from '@/composables/useScreenActivity';
import { useHardwareConfig } from '@/composables/useHardwareConfig';

// === Constants ===
const BOOT_TIMEOUT_MS = 2000;        // Show "connecting" after 2s (roughly when attempt 2 starts)
const BOOT_FAILED_MS = 12000;        // Show "unavailable" after 12s total
const LOGO_FADE_DELAY = 700;
const SCREEN_FADE_DELAY = 800;
const DOM_REMOVE_DELAY = 400;
const DOCK_AUTO_SHOW_DELAY = 500; // Show dock after

const { t } = useI18n();
const unifiedStore = useUnifiedAudioStore();
const podcastStore = usePodcastStore();
const settingsStore = useSettingsStore();
const multiroomStore = useMultiroomStore();
const dspStore = useDspStore();
const { on, onReconnect, onVisibilityChange, isConnected } = useWebSocket();
const { loadHardwareInfo } = useHardwareConfig();

// Enable screen activity detection (touch, mouse, keyboard)
useScreenActivity();

// === State ===
const isReady = ref(false);
const isBootComplete = ref(false);

// === Boot screen reference ===
let bootScreenEl = null;
let bootTimeoutId = null;
let bootFailedTimeoutId = null;

// === Boot timeout handling ===
function startBootTimeout() {
  clearBootTimeout();
  // Stage 1: Show "connecting" after first timeout
  bootTimeoutId = setTimeout(() => {
    if (!isReady.value) {
      showBootMessage(t('app.connecting'));
      // Stage 2: Show "unavailable" after more time
      bootFailedTimeoutId = setTimeout(() => {
        if (!isReady.value) {
          showBootMessage(t('app.connectionUnavailable'));
        }
      }, BOOT_FAILED_MS - BOOT_TIMEOUT_MS);
    }
  }, BOOT_TIMEOUT_MS);
}

function clearBootTimeout() {
  if (bootTimeoutId) {
    clearTimeout(bootTimeoutId);
    bootTimeoutId = null;
  }
  if (bootFailedTimeoutId) {
    clearTimeout(bootFailedTimeoutId);
    bootFailedTimeoutId = null;
  }
}

// === Boot message helpers ===
function showBootMessage(message) {
  if (!bootScreenEl) return;

  const textEl = bootScreenEl.querySelector('.boot-message-text');
  if (textEl) textEl.textContent = message;

  bootScreenEl.classList.add('show-message');
}

function hideBootMessage() {
  if (!bootScreenEl) return;
  bootScreenEl.classList.remove('show-message');
}

// === Reconnection screen (reuse boot screen) ===
function showReconnectionScreen() {
  if (!bootScreenEl) return;

  bootScreenEl.style.display = 'flex';
  bootScreenEl.classList.remove('fade-out', 'logo-exit');
  showBootMessage(t('app.connectionUnavailable'));
}

function hideReconnectionScreen() {
  if (!bootScreenEl) return;

  hideBootMessage();

  setTimeout(() => {
    bootScreenEl.classList.add('fade-out');
    setTimeout(() => {
      if (bootScreenEl) bootScreenEl.style.display = 'none';
    }, DOM_REMOVE_DELAY);
  }, 200);
}

// Watch isReady → trigger boot screen fade and dock auto-show
watch(isReady, (ready) => {
  if (ready && bootScreenEl) {
    clearBootTimeout();
    hideBootMessage();

    setTimeout(() => {
      bootScreenEl.classList.add('logo-exit');
    }, LOGO_FADE_DELAY);

    setTimeout(() => {
      bootScreenEl.classList.add('fade-out');
      isBootComplete.value = true;

      setTimeout(() => {
        if (bootScreenEl) bootScreenEl.style.display = 'none';
      }, DOM_REMOVE_DELAY);

      // Auto-show dock after boot complete
      setTimeout(() => {
        if (showDockFn) showDockFn();
      }, DOCK_AUTO_SHOW_DELAY);
    }, SCREEN_FADE_DELAY);
  }
});

// Watch connection lost AFTER boot complete
watch(isConnected, (connected) => {
  if (!isBootComplete.value) return;

  if (!connected) {
    showReconnectionScreen();
  } else {
    hideReconnectionScreen();
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

// Dock control registration (for auto-show on boot)
let showDockFn = null;
function registerDockControl(showFn) {
  showDockFn = showFn;
}

// Provide for child components
provide('openMultiroom', () => isMultiroomOpen.value = true);
provide('openSettings', openSettings);
provide('closeModals', () => {
  isMultiroomOpen.value = false;
  closeSettings();
});
provide('registerDockControl', registerDockControl);

const cleanupFunctions = [];

onMounted(async () => {
  // Initialize boot screen reference and start timeout
  bootScreenEl = document.getElementById('boot-screen');
  startBootTimeout();

  // Register WebSocket event listeners FIRST (before any async operations)
  // This prevents race condition where initial_state arrives before listeners are ready
  cleanupFunctions.push(
    on('system', 'initial_state', (event) => {
      clearBootTimeout();
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
    on('multiroom', 'client_state_changed', (event) => multiroomStore.handleMultiroomEvent(event)),
    on('multiroom', 'zone_changed', (event) => multiroomStore.handleMultiroomEvent(event)),
    on('multiroom', 'dsp_changed', (event) => dspStore.handleDspChanged(event)),
    on('multiroom', 'crossover_changed', (event) => dspStore.handleZoneCrossoverChanged(event)),
    onReconnect(() => {
      console.log('WebSocket reconnected');
      // Refresh registry state on reconnect (AC3: State Resync)
      multiroomStore.fetchState();
      // Refresh DSP state for current target
      dspStore.loadStatus();
    }),
    onVisibilityChange(() => {
      // Refresh stores when tab becomes visible (fixes stale data after background)
      multiroomStore.fetchState();
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
  multiroomStore.initialize();

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
  clearBootTimeout();
  cleanupFunctions.forEach(cleanup => cleanup());
});
</script>

<style>
.app-container {
  height: 100%;
}
</style>