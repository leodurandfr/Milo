<!-- frontend/src/views/MainView.vue -->
<template>
  <div class="main-view">
    <!-- Secret hotspot to open Settings -->
    <div class="SettingsAccess" role="button" aria-label="Open settings" @click="handleSettingsClick"></div>

    <div class="content-container">
      <AudioSourceView />
    </div>

    <Logo :position="logoPosition" :visible="logoVisible" />

    <AudioScreensaver
      :is-visible="isScreensaverVisible"
      :progress-converges="progressConverges"
      :artwork-rises="artworkRises"
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
import { computed, ref, watch, inject, provide, defineAsyncComponent } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useLyricsStore } from '@/stores/lyricsStore';
import { useCdStore } from '@/stores/cdStore';
import { useScreensaver } from '@/composables/useScreensaver';
import { SCREENSAVER_REVEAL_NONCE } from '@/composables/useScreensaverReveal';
import { useRichDisplay } from '@/composables/useRichDisplay';
import { useTimer } from '@/composables/useTimer';

import AudioSourceView from '@/components/audio/AudioSourceView.vue';
import Logo from '@/components/ui/Logo.vue';

// Lazy-loaded components
const AudioScreensaver = defineAsyncComponent(() =>
  import('@/components/audio/AudioScreensaver.vue')
);

const unifiedStore = useUnifiedAudioStore();
const lyricsStore = useLyricsStore();
const timer = useTimer();

// === Audio Screensaver ===
const { isScreensaverVisible, screensaverRevealNonce, screensaverData, screensaverProgress, closeScreensaver } = useScreensaver();

// Let revealed source views replay their entrance when the screensaver closes.
provide(SCREENSAVER_REVEAL_NONCE, screensaverRevealNonce);

// Whether the screensaver's progress bar should fly to AudioPlayerFull's bar
// position on close: only when the revealed player actually shows a bar there —
// Spotify always does, CD only when showing the player (not its tracklist).
const cdStore = useCdStore();
const progressConverges = computed(() => {
  const source = unifiedStore.systemState.active_source;
  return source === 'spotify' || (source === 'cd' && !cdStore.showTracklist);
});

// The screensaver artwork stays fixed only when the revealed view shows a cover
// at the same spot (the AudioPlayerFull sources). For the AudioSourceLayout
// sources there's no matching cover, so it rises + fades with the rest.
const artworkRises = computed(() =>
  ['radio', 'podcast', 'music_library'].includes(unifiedStore.systemState.active_source)
);

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
// Also hidden while Lyrics is open: it now renders as a content-container slot
// (see AudioSourceView), and the logo's fixed z-index would otherwise float
// over it whenever no rich player is behind Lyrics (e.g. active_source 'none').
const logoVisible = computed(() => richSource.value === null && !lyricsStore.isOpen);

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
// App.vue owns the settings modal and its open state — the pending-client
// auto-open guards on it, so a second local instance would be invisible to it.
const openSettings = inject('openSettings');

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