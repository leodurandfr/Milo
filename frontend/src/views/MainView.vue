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
        :metadata="unifiedStore.systemState.metadata"
        :is-disconnecting="disconnectingStates[unifiedStore.systemState.active_source]"
        @disconnect="handleDisconnect"
      />
    </div>

    <!-- Logo -->
    <Logo :position="logoPosition" :visible="logoVisible" />

    <!-- Settings modal -->
    <Modal :is-open="isSettingsOpen" @close="closeSettings" height-mode="auto">
      <SettingsModal @close="closeSettings" />
    </Modal>

    <!-- Audio Screensaver (Radio + Podcast) -->
    <AudioScreensaver
      :is-visible="isScreensaverVisible"
      :artwork="screensaverData.artwork"
      :title="screensaverData.title"
      :subtitle="screensaverData.subtitle"
      :station-favicon="screensaverData.stationFavicon"
      :station-name="screensaverData.stationName"
      :use-mono-subtitle="screensaverData.useMonoSubtitle"
      @close="closeScreensaver"
    />
  </div>
</template>

<script setup>
import { computed, ref, watch, onUnmounted, defineAsyncComponent } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useRadioStore } from '@/stores/radioStore';
import { usePodcastStore } from '@/stores/podcastStore';

import AudioSourceView from '@/components/audio/AudioSourceView.vue';
import Logo from '@/components/ui/Logo.vue';
import Modal from '@/components/ui/Modal.vue';

// Lazy-loaded components
const SettingsModal = defineAsyncComponent(() =>
  import('@/components/settings/SettingsModal.vue')
);
const AudioScreensaver = defineAsyncComponent(() =>
  import('@/components/audio/AudioScreensaver.vue')
);

const unifiedStore = useUnifiedAudioStore();
const radioStore = useRadioStore();
const podcastStore = usePodcastStore();

// === Disconnecting states for each plugin ===
const disconnectingStates = ref({
  bluetooth: false,
  mac: false,
  spotify: false,
  radio: false
});

// === Audio Screensaver ===
const isScreensaverVisible = ref(false);
let inactivityTimer = null;
const SCREENSAVER_DELAY = 15000; // 15 seconds

// Check if the screensaver should be active (radio or podcast playing)
const shouldMonitorInactivity = computed(() => {
  const source = unifiedStore.systemState.active_source;
  const state = unifiedStore.systemState.plugin_state;
  return (source === 'radio' || source === 'podcast') && state === 'connected';
});

// Resolve station favicon through backend proxy to avoid CORS
function stationArtworkUrl(station) {
  const favicon = station?.favicon;
  if (!favicon) return null;
  if (favicon.startsWith('/api/radio/images/')) return favicon;
  return `/api/radio/favicon?url=${encodeURIComponent(favicon)}`;
}

// Screensaver display data computed from active source
const screensaverData = computed(() => {
  const source = unifiedStore.systemState.active_source;

  if (source === 'radio') {
    const station = radioStore.currentStation;
    const track = radioStore.trackInfo;

    if (track) {
      // Radio with Shazam track recognition: show track info + station bar
      return {
        artwork: track.artwork || stationArtworkUrl(station),
        title: track.title,
        subtitle: track.artist || null,
        stationFavicon: stationArtworkUrl(station),
        stationName: station?.name || null
      };
    }

    // Radio without track recognition: show station metadata
    const genre = station?.genre
      ? station.genre.charAt(0).toUpperCase() + station.genre.slice(1)
      : null;
    const bitrate = station?.bitrate > 0 ? `${station.bitrate} kbps` : null;
    const metaParts = [genre, bitrate].filter(Boolean);

    return {
      artwork: stationArtworkUrl(station),
      title: station?.name || 'Unknown station',
      subtitle: metaParts.length > 0 ? metaParts.join(' \u2022 ') : 'Live',
      stationFavicon: null,
      stationName: null,
      useMonoSubtitle: true
    };
  }

  if (source === 'podcast') {
    const episode = podcastStore.displayEpisode;

    return {
      artwork: episode?.image_url || null,
      title: episode?.name || 'No episode',
      subtitle: episode?.podcast?.name || null,
      stationFavicon: null,
      stationName: null
    };
  }

  // Fallback (should not happen since shouldMonitorInactivity gates this)
  return {
    artwork: null,
    title: '',
    subtitle: null,
    stationFavicon: null,
    stationName: null
  };
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

// Watch the plugin state to start/stop monitoring
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
const lastVisiblePosition = ref('center');

const logoVisible = computed(() => {
  const { active_source, plugin_state, metadata, transitioning } = unifiedStore.systemState;

  // Visible during transition
  if (transitioning) {
    return true;
  }

  // Hidden: Spotify connected with track info
  if (active_source === 'spotify' && plugin_state === 'connected' && metadata?.title) {
    return false;
  }

  // Hidden: Radio or Podcast
  if (active_source === 'radio' || active_source === 'podcast') {
    return false;
  }

  return true;
});

const logoPosition = computed(() => {
  const { active_source, transitioning } = unifiedStore.systemState;

  // During transition, always top
  if (transitioning) {
    lastVisiblePosition.value = 'top';
    return 'top';
  }

  const newPosition = active_source === 'none' ? 'center' : 'top';

  // Only update position when visible
  if (logoVisible.value) {
    lastVisiblePosition.value = newPosition;
  }

  return lastVisiblePosition.value;
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
        // MAC disconnect not supported
        return;
      default:
        console.warn(`Disconnect not supported for ${currentSource}`);
        return;
    }

    if (response && response.ok) {
      const result = await response.json();
      if (result.status !== 'success' && !result.success) {
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