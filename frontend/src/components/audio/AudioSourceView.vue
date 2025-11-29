<!-- AudioSourceView.vue - Fixed version for centering and transitions -->
<template>
  <div class="audio-source-view">
    <!-- SIMPLIFIED transition without absolute positioning -->
    <Transition name="audio-content" mode="out-in">

      <!-- LibrespotView -->
      <div v-if="shouldShowLibrespot" :key="contentKey" class="librespot-container">
        <LibrespotSource />
      </div>

      <!-- RadioView -->
      <RadioSource v-else-if="shouldShowRadio" :key="contentKey" />

      <!-- PodcastView -->
      <PodcastSource v-else-if="shouldShowPodcast" :key="contentKey" />

      <!-- PluginStatus -->
      <div v-else-if="shouldShowPluginStatus" :key="contentKey" class="plugin-status-container">
        <AudioSourceStatus :plugin-type="currentPluginType" :plugin-state="currentPluginState"
          :device-name="currentDeviceName" :is-disconnecting="isDisconnecting" @disconnect="$emit('disconnect')" />
      </div>

    </Transition>
  </div>
</template>

<script setup>
import { computed, defineAsyncComponent } from 'vue';

const LibrespotSource = defineAsyncComponent(() =>
  import('../librespot/LibrespotSource.vue')
);
const RadioSource = defineAsyncComponent(() =>
  import('../radio/RadioSource.vue')
);
const PodcastSource = defineAsyncComponent(() =>
  import('../podcasts/PodcastSource.vue')
);
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
  return displayedSource.value === 'librespot' &&
    props.pluginState === 'connected' &&
    hasCompleteTrackInfo.value &&
    !props.transitioning;
});

const shouldShowRadio = computed(() => {
  return displayedSource.value === 'radio' &&
    !props.transitioning;
});

const shouldShowPodcast = computed(() => {
  return displayedSource.value === 'podcast' &&
    !props.transitioning;
});

const shouldShowPluginStatus = computed(() => {
  // Ne pas afficher le status lors d'une transition vers "none" (désactivation)
  if (props.transitioning && props.targetSource === 'none') {
    return false;
  }

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
  return displayedSource.value;
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

// Key for transitions - includes state for PluginStatus to animate between states
const contentKey = computed(() => {
  // Pour PluginStatus: inclure l'état pour déclencher les transitions entre états
  if (shouldShowPluginStatus.value) {
    return `${displayedSource.value}-${currentPluginState.value}-${!!currentDeviceName.value}`;
  }
  // Pour les vues complètes (Radio, Podcast, Librespot): la source suffit
  return displayedSource.value;
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

</style>