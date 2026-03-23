<!-- AudioSourceView.vue - Fixed version for centering and transitions -->
<template>
  <div class="audio-source-view">
    <!-- SIMPLIFIED transition without absolute positioning -->
    <Transition name="audio-content" mode="out-in" appear>

      <!-- SpotifyView -->
      <div v-if="shouldShowSpotify" :key="contentKey" class="spotify-container">
        <SpotifySource />
      </div>

      <!-- RadioView -->
      <RadioSource v-else-if="shouldShowRadio" :key="contentKey" />

      <!-- PodcastView -->
      <PodcastSource v-else-if="shouldShowPodcast" :key="contentKey" />

      <!-- CDView -->
      <CDSource v-else-if="shouldShowCD" :key="contentKey" />

      <!-- AirPlayView -->
      <div v-else-if="shouldShowAirPlay" :key="contentKey" class="connect-container">
        <AirPlaySource />
      </div>

      <!-- PluginStatus -->
      <div v-else-if="shouldShowPluginStatus" :key="contentKey" class="plugin-status-container">
        <AudioSourceStatus :plugin-type="currentPluginType" :plugin-state="currentPluginState"
          :device-name="currentDeviceName" :is-disconnecting="isDisconnecting" @disconnect="handleDisconnect" />
      </div>

    </Transition>
  </div>
</template>

<script setup>
import { computed, ref, watch, onUnmounted, defineAsyncComponent } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';

const SpotifySource = defineAsyncComponent(() =>
  import('../spotify/SpotifySource.vue')
);
const RadioSource = defineAsyncComponent(() =>
  import('../radio/RadioSource.vue')
);
const PodcastSource = defineAsyncComponent(() =>
  import('../podcasts/PodcastSource.vue')
);
const AirPlaySource = defineAsyncComponent(() =>
  import('../airplay/AirPlaySource.vue')
);
const CDSource = defineAsyncComponent(() =>
  import('../cd/CDSource.vue')
);
import AudioSourceStatus from './AudioSourceStatus.vue';

const unifiedStore = useUnifiedAudioStore();

// Read all state directly from the store
const activeSource = computed(() => unifiedStore.systemState.active_source);
const pluginState = computed(() => unifiedStore.systemState.plugin_state);
const transitioning = computed(() => unifiedStore.systemState.transitioning);
const metadata = computed(() => unifiedStore.systemState.metadata);

// === DISCONNECT LOGIC ===
const isDisconnecting = computed(() => unifiedStore.isDisconnecting(activeSource.value));

function handleDisconnect() {
  unifiedStore.disconnectSource(activeSource.value);
}

// === DECISION LOGIC ===
const hasCompleteTrackInfo = computed(() => {
  return !!(
    pluginState.value === 'active' &&
    metadata.value?.title &&
    metadata.value?.artist
  );
});

const shouldShowSpotify = computed(() => {
  return activeSource.value === 'spotify' &&
    pluginState.value === 'active' &&
    hasCompleteTrackInfo.value &&
    !transitioning.value;
});

const shouldShowRadio = computed(() => {
  return activeSource.value === 'radio' &&
    !transitioning.value;
});

const shouldShowPodcast = computed(() => {
  return activeSource.value === 'podcast' &&
    !transitioning.value;
});

const shouldShowCD = computed(() => {
  return activeSource.value === 'cd' &&
    pluginState.value === 'active' &&
    !transitioning.value;
});

const shouldShowAirPlay = computed(() => {
  return activeSource.value === 'airplay' &&
    pluginState.value === 'active' &&
    hasCompleteTrackInfo.value &&
    !transitioning.value;
});

const shouldShowPluginStatus = computed(() => {
  // Don't show status during transition to "none" (deactivation)
  if (transitioning.value && activeSource.value === 'none') {
    return false;
  }

  // Transition in progress
  if (transitioning.value) return true;

  // bluetooth/mac sources
  if (['bluetooth', 'mac'].includes(activeSource.value)) return true;

  // Spotify without complete conditions
  if (activeSource.value === 'spotify') {
    return !hasCompleteTrackInfo.value || pluginState.value !== 'active';
  }

  // AirPlay without complete conditions
  if (activeSource.value === 'airplay') {
    return !hasCompleteTrackInfo.value || pluginState.value !== 'active';
  }

  // CD: show status during starting/waiting (not yet active)
  if (activeSource.value === 'cd') {
    return pluginState.value !== 'active';
  }

  return false;
});

// === PROPERTIES FOR PLUGINSTATUS ===
const currentPluginType = computed(() => activeSource.value);

const rawPluginState = computed(() => {
  if (transitioning.value) return 'starting';
  // CD: disc present but cache still loading (WAITING state, not yet ACTIVE)
  if (activeSource.value === 'cd' && pluginState.value === 'waiting' &&
      metadata.value?.disc_present && !metadata.value?.cache_ready) {
    return 'loading_disc';
  }
  return pluginState.value;
});

// Minimum display time for "starting" state (prevents flash before "loading_disc")
const STARTING_MIN_MS = 2000;
const currentPluginState = ref(rawPluginState.value);
let startingEnteredAt = null;
let startingTimer = null;

watch(rawPluginState, (newState, oldState) => {
  clearTimeout(startingTimer);

  if (newState === 'starting') {
    startingEnteredAt = Date.now();
    currentPluginState.value = 'starting';
    return;
  }

  // Leaving "starting" — enforce minimum display time
  if (oldState === 'starting' && startingEnteredAt) {
    const remaining = STARTING_MIN_MS - (Date.now() - startingEnteredAt);
    if (remaining > 0) {
      startingTimer = setTimeout(() => {
        currentPluginState.value = rawPluginState.value;
        startingEnteredAt = null;
      }, remaining);
      return;
    }
  }

  startingEnteredAt = null;
  currentPluginState.value = newState;
});

onUnmounted(() => clearTimeout(startingTimer));

const currentDeviceName = computed(() => {
  const meta = metadata.value || {};

  switch (activeSource.value) {
    case 'bluetooth':
      return meta.device_name || '';
    case 'mac':
      return meta.client_names || meta.client_name || [];
    case 'airplay':
      return meta.client_name || '';
    default:
      return '';
  }
});

// Key for transitions - includes state for PluginStatus to animate between states
const contentKey = computed(() => {
  if (shouldShowPluginStatus.value) {
    return `${activeSource.value}-${currentPluginState.value}-${!!currentDeviceName.value}`;
  }
  return activeSource.value;
});

</script>

<style scoped>
.audio-source-view {
  width: 100%;
  height: 100%;
  display: flex;
  /* padding: 0 var(--space-06); */
  justify-content: center;
  position: relative;
}

/* === CONTAINERS FOR SPECIFIC LAYOUTS === */

/* === SIMPLIFIED CONTAINERS === */

/* Connect-style sources: natural full-screen */
.spotify-container,
.connect-container {
  width: 100%;
  height: 100%;
}

/* PluginStatus: naturally centered */
.plugin-status-container {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-05);
}

/* === TRANSITIONS WITH FORCED DIRECTIONS === */

.audio-content-enter-active {
  transition: all var(--transition-spring);
}

.audio-content-leave-active {
  transition: all var(--transition-fast-leave);
}

/* Default direction: ALWAYS bottom to top */
.audio-content-enter-from {
  opacity: 0;
  transform: translateY(var(--space-06)) scale(0.98);
}

.audio-content-leave-to {
  opacity: 0;
  transform: translateY(calc(-1 * var(--space-06))) scale(0.98);
}

.audio-content-enter-to,
.audio-content-leave-from {
  opacity: 1;
  transform: translateY(0) scale(1);
}

</style>