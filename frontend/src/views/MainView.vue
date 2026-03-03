<!-- frontend/src/views/MainView.vue -->
<template>
  <div class="main-view">
    <!-- Secret hotspot to open Settings -->
    <div class="SettingsAccess" role="button" aria-label="Open settings" @click="handleSettingsClick"></div>

    <!-- Main content -->
    <div class="content-container">
      <AudioSourceView />
    </div>

    <!-- Logo -->
    <Logo :position="logoPosition" :visible="logoVisible" />

    <!-- Settings modal -->
    <Modal :is-open="isSettingsOpen" @close="closeSettings" height-mode="auto">
      <SettingsModal @close="closeSettings" />
    </Modal>

    <!-- Audio Screensaver -->
    <AudioScreensaver
      :is-visible="isScreensaverVisible"
      :mode="screensaverData.mode || 'media'"
      :plugin-type="screensaverData.pluginType"
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
import { useSettingsStore } from '@/stores/settingsStore';

import { useI18n } from '@/services/i18n';
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

const { t } = useI18n();
const unifiedStore = useUnifiedAudioStore();
const settingsStore = useSettingsStore();
const radioStore = useRadioStore();
const podcastStore = usePodcastStore();

// === Audio Screensaver ===
const isScreensaverVisible = ref(false);
let inactivityTimer = null;
// Screensaver delay from settings (in ms)
const screensaverDelay = computed(() =>
  (settingsStore.screenScreensaver.screensaver_delay_seconds ?? 15) * 1000
);

// Check if the screensaver should be active (enabled + supported source connected)
const shouldMonitorInactivity = computed(() => {
  if (!settingsStore.screenScreensaver.screensaver_enabled) return false;
  const source = unifiedStore.systemState.active_source;
  const state = unifiedStore.systemState.plugin_state;
  return ['radio', 'podcast', 'bluetooth', 'mac'].includes(source) && state === 'connected';
});

// Resolve station favicon through backend proxy to avoid CORS
function stationArtworkUrl(station) {
  const favicon = station?.favicon;
  if (!favicon) return null;
  if (favicon.startsWith('/api/radio/images/')) return favicon;
  return `/api/radio/favicon?url=${encodeURIComponent(favicon)}`;
}

// Device name formatting for screensaver display
function cleanDeviceName(name) {
  if (!name) return '';
  return name.replace('.local', '').replace(/-/g, ' ');
}

function formatDeviceNames(deviceName) {
  if (!deviceName) return '';
  if (Array.isArray(deviceName)) {
    if (deviceName.length === 0) return '';
    return deviceName.map(n => cleanDeviceName(n)).join('\n');
  }
  return cleanDeviceName(deviceName);
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
        mode: 'media',
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
      mode: 'media',
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
      mode: 'media',
      artwork: episode?.image_url || null,
      title: episode?.name || 'No episode',
      subtitle: episode?.podcast?.name || null,
      stationFavicon: null,
      stationName: null
    };
  }

  if (source === 'bluetooth') {
    const metadata = unifiedStore.systemState.metadata || {};
    return {
      mode: 'simple',
      pluginType: 'bluetooth',
      title: t('status.connectedTo'),
      subtitle: formatDeviceNames(metadata.device_name),
    };
  }

  if (source === 'mac') {
    const metadata = unifiedStore.systemState.metadata || {};
    return {
      mode: 'simple',
      pluginType: 'mac',
      title: t('status.audioReceivedFrom'),
      subtitle: formatDeviceNames(metadata.client_names || metadata.client_name),
    };
  }

  // Fallback (should not happen since shouldMonitorInactivity gates this)
  return {
    mode: 'media',
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
  }, screensaverDelay.value);
}

// Clear the inactivity timer
function clearInactivityTimer() {
  if (inactivityTimer) {
    clearTimeout(inactivityTimer);
    inactivityTimer = null;
  }
}

// User activity handler (throttled to avoid excessive calls from high-frequency events)
let lastActivityTime = 0;
const ACTIVITY_THROTTLE_MS = 500;

function handleUserActivity() {
  const now = Date.now();
  if (now - lastActivityTime < ACTIVITY_THROTTLE_MS) return;
  lastActivityTime = now;

  if (!isScreensaverVisible.value) {
    resetInactivityTimer();
  }
}

// Add activity listeners (pointerdown/touchstart sufficient for screensaver reset)
function addActivityListeners() {
  document.addEventListener('pointerdown', handleUserActivity, { passive: true });
  document.addEventListener('wheel', handleUserActivity, { passive: true });
  document.addEventListener('touchstart', handleUserActivity, { passive: true });
}

// Remove activity listeners
function removeActivityListeners() {
  document.removeEventListener('pointerdown', handleUserActivity);
  document.removeEventListener('wheel', handleUserActivity);
  document.removeEventListener('touchstart', handleUserActivity);
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

// Reset timer when delay setting changes
watch(() => settingsStore.screenScreensaver.screensaver_delay_seconds, () => {
  if (shouldMonitorInactivity.value && !isScreensaverVisible.value) {
    resetInactivityTimer();
  }
});

// === LOGO STATE ===
const lastVisiblePosition = ref('center');

const logoVisible = computed(() => {
  const { active_source, plugin_state, metadata, transitioning } = unifiedStore.systemState;

  // Visible during transition
  if (transitioning) {
    return true;
  }

  // Hidden: Spotify/AirPlay connected with track info
  if ((active_source === 'spotify' || active_source === 'airplay') && plugin_state === 'connected' && metadata?.title) {
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