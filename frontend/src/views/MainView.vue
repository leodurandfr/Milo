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
      :source-type="screensaverData.sourceType"
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
import { useScreensaver } from '@/composables/useScreensaver';

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

// === Audio Screensaver ===
const { isScreensaverVisible, screensaverData, closeScreensaver } = useScreensaver();

// === LOGO STATE ===
const lastVisiblePosition = ref('center');

const logoVisible = computed(() => {
  const { active_source, source_state, metadata, transitioning } = unifiedStore.systemState;

  // Visible during transition
  if (transitioning) {
    return true;
  }

  // Hidden: Spotify/AirPlay connected with track info
  if ((active_source === 'spotify' || active_source === 'airplay') && source_state === 'active' && metadata?.title) {
    return false;
  }

  // Hidden: Radio, Podcast, or CD
  if (active_source === 'radio' || active_source === 'podcast' || active_source === 'cd') {
    return false;
  }

  return true;
});

// Update cached position only when logo is visible or transitioning
watch(
  () => ({
    active_source: unifiedStore.systemState.active_source,
    transitioning: unifiedStore.systemState.transitioning,
    visible: logoVisible.value
  }),
  ({ active_source, transitioning, visible }) => {
    if (transitioning) {
      lastVisiblePosition.value = 'top';
    } else if (visible) {
      lastVisiblePosition.value = active_source === 'none' ? 'center' : 'top';
    }
  },
  { immediate: true }
);

const logoPosition = computed(() => lastVisiblePosition.value);

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
    openSettings();
  }
}

onUnmounted(() => {
  if (clickWindowTimer) clearTimeout(clickWindowTimer);
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