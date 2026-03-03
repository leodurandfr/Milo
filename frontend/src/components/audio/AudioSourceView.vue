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
import { computed, ref, defineAsyncComponent } from 'vue';
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
import AudioSourceStatus from './AudioSourceStatus.vue';

const unifiedStore = useUnifiedAudioStore();

// Read all state directly from the store
const activeSource = computed(() => unifiedStore.systemState.active_source);
const pluginState = computed(() => unifiedStore.systemState.plugin_state);
const transitioning = computed(() => unifiedStore.systemState.transitioning);
const metadata = computed(() => unifiedStore.systemState.metadata);

// === DISCONNECT LOGIC ===
const disconnectingStates = ref({
  bluetooth: false,
  mac: false,
  spotify: false,
  radio: false
});

const isDisconnecting = computed(() => disconnectingStates.value[activeSource.value] || false);

async function handleDisconnect() {
  const currentSource = activeSource.value;
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
        return;
      default:
        console.warn(`Disconnect not supported for ${currentSource}`);
        return;
    }

    if (response && !response.ok) {
      const result = await response.json();
      console.error(`Disconnect error: ${result.detail}`);
    }
  } catch (error) {
    console.error(`Error disconnecting ${currentSource}:`, error);
  } finally {
    setTimeout(() => {
      disconnectingStates.value[currentSource] = false;
    }, 900);
  }
}

// === DECISION LOGIC ===
const hasCompleteTrackInfo = computed(() => {
  return !!(
    pluginState.value === 'connected' &&
    metadata.value?.title &&
    metadata.value?.artist
  );
});

const shouldShowSpotify = computed(() => {
  return activeSource.value === 'spotify' &&
    pluginState.value === 'connected' &&
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

const shouldShowAirPlay = computed(() => {
  return activeSource.value === 'airplay' &&
    pluginState.value === 'connected' &&
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
    return !hasCompleteTrackInfo.value || pluginState.value !== 'connected';
  }

  // AirPlay without complete conditions
  if (activeSource.value === 'airplay') {
    return !hasCompleteTrackInfo.value || pluginState.value !== 'connected';
  }

  return false;
});

// === PROPERTIES FOR PLUGINSTATUS ===
const currentPluginType = computed(() => activeSource.value);

const currentPluginState = computed(() => {
  if (transitioning.value) return 'starting';
  return pluginState.value;
});

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
  transition: all var(--transition-fast);
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