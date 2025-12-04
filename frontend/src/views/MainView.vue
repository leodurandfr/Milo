<!-- frontend/src/views/MainView.vue -->
<template>
  <div class="main-view">
    <!-- Secret hotspot to open Settings -->
    <div class="SettingsAccess" role="button" aria-label="Open settings" @click="handleSettingsClick"></div>

    <!-- Main content -->
    <div class="content-container">
      <AudioSourceView
        :active-source="unifiedStore.systemState.active_source"
        :plugin-state="unifiedStore.systemState.plugin_state"
        :transitioning="unifiedStore.systemState.transitioning"
        :target-source="unifiedStore.systemState.target_source"
        :metadata="unifiedStore.systemState.metadata"
        :is-disconnecting="disconnectingStates[unifiedStore.systemState.active_source]"
        @disconnect="handleDisconnect"
      />
    </div>

    <!-- Logo -->
    <Logo :state="logoState" />

    <!-- Settings modal -->
    <Modal :is-open="isSettingsOpen" @close="closeSettings" height-mode="auto">
      <SettingsModal @close="closeSettings" />
    </Modal>

    <!-- Radio Screensaver -->
    <RadioScreensaver :is-visible="isScreensaverVisible" @close="closeScreensaver" />
  </div>
</template>

<script setup>
import { computed, ref, watch, onUnmounted, defineAsyncComponent } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';

import AudioSourceView from '@/components/audio/AudioSourceView.vue';
import Logo from '@/components/ui/Logo.vue';
import Modal from '@/components/ui/Modal.vue';

// Lazy-loaded components
const SettingsModal = defineAsyncComponent(() =>
  import('@/components/settings/SettingsModal.vue')
);
const RadioScreensaver = defineAsyncComponent(() =>
  import('@/components/radio/RadioScreensaver.vue')
);

const unifiedStore = useUnifiedAudioStore();

// === Disconnecting states for each plugin ===
const disconnectingStates = ref({
  bluetooth: false,
  mac: false,
  spotify: false,
  radio: false
});

// === Radio Screensaver ===
const isScreensaverVisible = ref(false);
let inactivityTimer = null;
const SCREENSAVER_DELAY = 15000; // 15 seconds

// Check if the screensaver should be active
const shouldMonitorInactivity = computed(() => {
  return unifiedStore.systemState.active_source === 'radio' &&
         unifiedStore.systemState.plugin_state === 'connected';
});

// Reset the inactivity timer
function resetInactivityTimer() {
  clearInactivityTimer();

  if (!shouldMonitorInactivity.value || isScreensaverVisible.value) {
    return;
  }

  inactivityTimer = setTimeout(() => {
    isScreensaverVisible.value = true;
  }, SCREENSAVER_DELAY);
}

// Clear the inactivity timer
function clearInactivityTimer() {
  if (inactivityTimer) {
    clearTimeout(inactivityTimer);
    inactivityTimer = null;
  }
}

// User activity handler
function handleUserActivity() {
  if (!isScreensaverVisible.value) {
    resetInactivityTimer();
  }
}

// Add activity listeners
function addActivityListeners() {
  document.addEventListener('pointermove', handleUserActivity, { passive: true });
  document.addEventListener('pointerdown', handleUserActivity, { passive: true });
  document.addEventListener('wheel', handleUserActivity, { passive: true });
  document.addEventListener('touchstart', handleUserActivity, { passive: true });
  document.addEventListener('touchmove', handleUserActivity, { passive: true });
}

// Remove activity listeners
function removeActivityListeners() {
  document.removeEventListener('pointermove', handleUserActivity, { passive: true });
  document.removeEventListener('pointerdown', handleUserActivity, { passive: true });
  document.removeEventListener('wheel', handleUserActivity, { passive: true });
  document.removeEventListener('touchstart', handleUserActivity, { passive: true });
  document.removeEventListener('touchmove', handleUserActivity, { passive: true });
}

// Close the screensaver
function closeScreensaver() {
  isScreensaverVisible.value = false;
  resetInactivityTimer();
}

// Watch the radio plugin state to start/stop monitoring
watch(shouldMonitorInactivity, (shouldMonitor) => {
  if (shouldMonitor) {
    addActivityListeners();
    resetInactivityTimer();
  } else {
    removeActivityListeners();
    clearInactivityTimer();
    isScreensaverVisible.value = false;
  }
}, { immediate: true });

// === LOGO STATE ===
const logoState = computed(() => {
  const { active_source, plugin_state, metadata, transitioning } = unifiedStore.systemState;

  // During transition → always at top
  if (transitioning) {
    return 'top';
  }

  // Spotify connected with track info → hidden
  if (active_source === 'spotify' &&
      plugin_state === 'connected' &&
      metadata?.title &&
      metadata?.artist) {
    return 'hidden';
  }

  // Radio or Podcast → hidden
  if (active_source === 'radio' || active_source === 'podcast') {
    return 'hidden';
  }

  // No active plugin → centered
  if (active_source === 'none') {
    return 'center';
  }

  // All other cases (bluetooth, mac, spotify waiting) → at top
  return 'top';
});

// === ACTION HANDLERS ===
async function handleDisconnect() {
  const currentSource = unifiedStore.systemState.active_source;
  if (!currentSource || currentSource === 'none') return;

  disconnectingStates.value[currentSource] = true;

  try {
    let response;

    switch (currentSource) {
      case 'bluetooth':
        response = await fetch('/api/bluetooth/disconnect', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' }
        });
        break;
      case 'mac':
        console.log('MAC disconnect not implemented');
        return;
      default:
        console.warn(`Disconnect not supported for ${currentSource}`);
        return;
    }

    if (response && response.ok) {
      const result = await response.json();
      if (result.status === 'success' || result.success) {
        console.log(`${currentSource} disconnected successfully`);
      } else {
        console.error(`Disconnect error: ${result.message || result.error}`);
      }
    }
  } catch (error) {
    console.error(`Error disconnecting ${currentSource}:`, error);
  } finally {
    setTimeout(() => {
      disconnectingStates.value[currentSource] = false;
    }, 900);
  }
}

/* =========================
   Settings access (secret tap)
   ========================= */
const isSettingsOpen = ref(false);

function openSettings() {
  isSettingsOpen.value = true;
}
function closeSettings() {
  isSettingsOpen.value = false;
}

const SETTINGS_CLICKS_REQUIRED = 5;
const CLICK_WINDOW_MS = 5000;

const settingsClicks = ref(0);
let clickWindowTimer = null;

function resetClickWindow() {
  settingsClicks.value = 0;
  if (clickWindowTimer) {
    clearTimeout(clickWindowTimer);
    clickWindowTimer = null;
  }
}

function handleSettingsClick() {
  if (settingsClicks.value === 0) {
    clickWindowTimer = setTimeout(() => {
      resetClickWindow();
    }, CLICK_WINDOW_MS);
  }

  settingsClicks.value += 1;

  if (settingsClicks.value >= SETTINGS_CLICKS_REQUIRED) {
    resetClickWindow();
    openSettings(); // ✅ Open the modal instead of navigating to /settings
  }
}

onUnmounted(() => {
  // Cleanup when leaving the view
  if (clickWindowTimer) clearTimeout(clickWindowTimer);

  // Screensaver cleanup
  removeActivityListeners();
  clearInactivityTimer();
});
</script>

<style scoped>
.main-view {
  background: var(--color-background);
  height: 100%;
  position: relative;
}

.content-container {
  width: 100%;
  height: 100%;
  position: relative;
  z-index: 1;
}

.SettingsAccess {
  position: absolute;
  top: 0;
  right: 0;
  width: 32px;
  height: 64px;
  z-index: 9999;
}
</style>