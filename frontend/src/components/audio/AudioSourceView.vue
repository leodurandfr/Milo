<!-- AudioSourceView.vue - Fixed version for centering and transitions -->
<template>
  <div class="audio-source-view">
    <!-- SIMPLIFIED transition without absolute positioning -->
    <Transition name="audio-content" mode="out-in">

      <!-- LibrespotView with forced key -->
      <div v-if="shouldShowLibrespot" :key="librespotKey" class="librespot-container">
        <LibrespotSource />
      </div>

      <!-- RadioView -->
      <RadioSource v-else-if="shouldShowRadio" :key="radioKey" />

      <!-- PodcastView -->
      <PodcastSource v-else-if="shouldShowPodcast" :key="podcastKey" />

      <!-- PluginStatus -->
      <div v-else-if="shouldShowPluginStatus" :key="pluginStatusKey" class="plugin-status-container">
        <AudioSourceStatus :plugin-type="currentPluginType" :plugin-state="currentPluginState"
          :device-name="currentDeviceName" :is-disconnecting="isDisconnecting" @disconnect="$emit('disconnect')" />
      </div>

    </Transition>
  </div>
</template>

<script setup>
import { computed, ref, onMounted } from 'vue';
import LibrespotSource from '../librespot/LibrespotSource.vue';
import RadioSource from '../radio/RadioSource.vue';
import PodcastSource from './PodcastSource.vue';
import AudioSourceStatus from './AudioSourceStatus.vue';

// Props
const props = defineProps({
  activeSource: {
    type: String,
    required: true
  },
  pluginState: {
    type: String,
    required: true
  },
  transitioning: {
    type: Boolean,
    default: false
  },
  targetSource: {
    type: String,
    default: null
  },
  metadata: {
    type: Object,
    default: () => ({})
  },
  isDisconnecting: {
    type: Boolean,
    default: false
  }
});

// Emits
const emit = defineEmits(['disconnect']);

// Initial waiting state (1000ms)
const showInitialDelay = ref(true);

// === SIMPLIFIED DECISION LOGIC ===
const displayedSource = computed(() => {
  if (props.transitioning && props.targetSource) {
    return props.targetSource;
  }
  return props.activeSource;
});

const hasCompleteTrackInfo = computed(() => {
  return !!(
    props.pluginState === 'connected' &&
    props.metadata?.title &&
    props.metadata?.artist
  );
});

const shouldShowLibrespot = computed(() => {
  if (showInitialDelay.value) return false;

  return displayedSource.value === 'librespot' &&
    props.pluginState === 'connected' &&
    hasCompleteTrackInfo.value &&
    !props.transitioning;
});

const shouldShowRadio = computed(() => {
  if (showInitialDelay.value) return false;

  return displayedSource.value === 'radio' &&
    !props.transitioning;
});

const shouldShowPodcast = computed(() => {
  if (showInitialDelay.value) return false;

  return displayedSource.value === 'podcast' &&
    !props.transitioning;
});

const shouldShowPluginStatus = computed(() => {
  if (showInitialDelay.value) return false;

  // Transition in progress
  if (props.transitioning) return true;

  // bluetooth/roc sources
  if (['bluetooth', 'roc'].includes(displayedSource.value)) return true;

  // Librespot without complete conditions
  if (displayedSource.value === 'librespot') {
    return !hasCompleteTrackInfo.value || props.pluginState !== 'connected';
  }

  return false;
});

// === PROPERTIES FOR PLUGINSTATUS ===
const currentPluginType = computed(() => {
  // Avoid "none" which is not a valid value for components
  const source = displayedSource.value;
  return source === 'none' ? 'librespot' : source;
});

const currentPluginState = computed(() => {
  if (props.transitioning) return 'starting';
  return props.pluginState;
});

const currentDeviceName = computed(() => {
  const metadata = props.metadata || {};

  switch (displayedSource.value) {
    case 'bluetooth':
      return metadata.device_name || '';
    case 'roc':
      // Support multiple clients: return the array or a single string
      return metadata.client_names || metadata.client_name || [];
    default:
      return '';
  }
});

// FORCED key to guarantee ready ↔ connected transitions
const pluginStatusKey = computed(() => {
  return `${currentPluginType.value}-${currentPluginState.value}-${!!currentDeviceName.value}`;
});

// Specific key for LibrespotView to force transitions
const librespotKey = computed(() => {
  // Change key when switching from PluginStatus to LibrespotView
  return shouldShowLibrespot.value ? 'librespot-connected' : 'librespot-hidden';
});

// Specific key for RadioView
const radioKey = computed(() => {
  return shouldShowRadio.value ? 'radio-active' : 'radio-hidden';
});

// Specific key for PodcastView
const podcastKey = computed(() => {
  return shouldShowPodcast.value ? 'podcast-active' : 'podcast-hidden';
});

// === LIFECYCLE ===
onMounted(() => {
  // console.log('🚀 AudioSourceView mounted - SIMPLIFIED');

  // Initial 1000ms wait
  setTimeout(() => {
    showInitialDelay.value = false;
  }, 1000);
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

/* LibrespotView: natural full-screen */
.librespot-container {
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

/* Extra force for LibrespotView with maximum priority */
.librespot-container {
  /* Default position reset to avoid memorization */
  transform: translateY(0) scale(1);
}

.librespot-container.audio-content-enter-from,
.audio-content-enter-from .librespot-container {
  opacity: 0 !important;
  transform: translateY(var(--space-06)) scale(0.98) !important;
}

.librespot-container.audio-content-leave-to,
.audio-content-leave-to .librespot-container {
  opacity: 0 !important;
  transform: translateY(calc(-1 * var(--space-06))) scale(0.98) !important;
}
</style>