<!-- frontend/src/views/MainView.vue -->
<template>
  <div class="main-view">
    <!-- Secret hotspot to open Settings -->
    <div class="SettingsAccess" role="button" aria-label="Open settings" @click="handleSettingsClick"></div>

    <div class="content-container">
      <AudioSourceView />
    </div>

    <Logo :position="logoPosition" :visible="logoVisible" />

    <Modal :is-open="isSettingsOpen" @close="closeSettings" height-mode="auto">
      <SettingsModal @close="closeSettings" />
    </Modal>

    <AudioScreensaver
      :is-visible="isScreensaverVisible"
      :mode="screensaverData.mode || 'media'"
      :source-type="screensaverData.sourceType"
      :artwork="screensaverData.artwork"
      :title="screensaverData.title"
      :subtitle="screensaverData.subtitle"
      :station-favicon="screensaverData.stationFavicon"
      :station-icon="screensaverData.stationIcon"
      :station-name="screensaverData.stationName"
      :use-mono-subtitle="screensaverData.useMonoSubtitle"
      :progress="screensaverProgress"
      @close="closeScreensaver"
    />
  </div>
</template>

<script setup>
import { computed, ref, watch, inject, defineAsyncComponent } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useScreensaver } from '@/composables/useScreensaver';
import { useRichDisplay } from '@/composables/useRichDisplay';
import { useTimer } from '@/composables/useTimer';

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
const timer = useTimer();

// === Audio Screensaver ===
const { isScreensaverVisible, screensaverData, screensaverProgress, closeScreensaver } = useScreensaver();

// Dismiss screensaver when App.vue signals (e.g., new pending client detected)
const dismissScreensaverSignal = inject('dismissScreensaver', ref(0));
watch(dismissScreensaverSignal, () => {
  if (isScreensaverVisible.value) closeScreensaver();
});

// === LOGO STATE ===
const lastVisiblePosition = ref('center');

// The logo is hidden exactly when the active source shows a rich full-screen
// player (its dedicated component / AudioPlayerFull / AudioSourceLayout). Every
// other case keeps it visible: idle, source transitions, and any source on the
// AudioSourceStatus card — Bluetooth/Mac, CD while loading/ejecting/no-drive,
// and AirPlay before real playback starts. `richSource` is shared with
// AudioSourceView (which picks the component to render), so the logo can't
// drift out of sync with what's actually on screen.
const { richSource } = useRichDisplay();
const logoVisible = computed(() => richSource.value === null);

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
    timer.clear(clickWindowTimer);
    clickWindowTimer = null;
  }
}

function handleSettingsClick() {
  if (settingsClicks.value === 0) {
    clickWindowTimer = timer.setTimeout(() => {
      resetClickWindow();
    }, CLICK_WINDOW_MS);
  }

  settingsClicks.value += 1;

  if (settingsClicks.value >= SETTINGS_CLICKS_REQUIRED) {
    resetClickWindow();
    openSettings();
  }
}
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