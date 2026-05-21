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

      <!-- Source Status -->
      <div v-else-if="shouldShowSourceStatus" :key="contentKey" class="source-status-container">
        <AudioSourceStatus :source-type="currentSourceType" :source-state="currentSourceState"
          :device-name="currentDeviceName" :is-disconnecting="isDisconnecting" @disconnect="handleDisconnect" />
      </div>

    </Transition>
  </div>
</template>

<script setup>
import { computed, ref, watch, defineAsyncComponent } from 'vue';
import { useTimer } from '@/composables/useTimer';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { AIRPLAY_MIN_ARTWORK_PX } from '@/constants/imageQuality';

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
const sourceState = computed(() => unifiedStore.systemState.source_state);
const transitioning = computed(() => unifiedStore.systemState.transitioning);
const metadata = computed(() => unifiedStore.systemState.metadata);

// === DISCONNECT LOGIC ===
const isDisconnecting = computed(() => unifiedStore.isDisconnecting(activeSource.value));

function handleDisconnect() {
  unifiedStore.disconnectSource(activeSource.value);
}

// === DISPLAY DECISION (single source of truth) ===
//
// Each active source either renders its rich full-screen view (its dedicated
// component / AudioPlayerFull) or falls back to AudioSourceStatus (the
// conservative "Connecté à…" card). `hasRichDisplay` is the ONE place that
// rule lives — keep per-source conditions here, not scattered across booleans.

// AirPlay's artwork-quality gate (AIRPLAY_MIN_ARTWORK_PX) is shared with the
// screensaver — see @/constants/imageQuality.

function hasRichDisplay(source, state, meta) {
  const m = meta || {};
  switch (source) {
    case 'spotify':
      // Trusted metadata provider: title + artist is enough.
      return state === 'active' && !!m.title && !!m.artist;
    case 'airplay':
      // Untrusted sender: require title, artist AND a real cover (>300px).
      // A small/absent image means browser audio → status card.
      // Passive source (no Milō controls), so also require audio to be flowing:
      // when the sender stops/quits, the route stays connected and the backend
      // keeps the stale cover but flips is_playing=false → drop to the status
      // card rather than freeze on a cover for audio that no longer plays.
      return state === 'active' && !!m.is_playing && !!m.title && !!m.artist &&
        (m.album_art_width || 0) > AIRPLAY_MIN_ARTWORK_PX;
    case 'radio':
    case 'podcast':
      // Own component handles internal empty/loading states.
      return true;
    case 'cd':
      return state === 'active';
    default:
      // bluetooth, mac, none → no rich view, always status.
      return false;
  }
}

// The active source resolves to a rich view, or null (→ status fallback).
// Transitions always defer to the status card.
const richSource = computed(() =>
  !transitioning.value &&
  hasRichDisplay(activeSource.value, sourceState.value, metadata.value)
    ? activeSource.value
    : null
);

const shouldShowSpotify = computed(() => richSource.value === 'spotify');
const shouldShowRadio = computed(() => richSource.value === 'radio');
const shouldShowPodcast = computed(() => richSource.value === 'podcast');
const shouldShowCD = computed(() => richSource.value === 'cd');
const shouldShowAirPlay = computed(() => richSource.value === 'airplay');

const shouldShowSourceStatus = computed(() => {
  if (activeSource.value === 'none') return false;  // nothing active (incl. deactivation)
  if (transitioning.value) return true;             // status card during transitions
  return richSource.value === null;                 // active source without a rich view
});

// === PROPERTIES FOR SOURCE STATUS ===
const currentSourceType = computed(() => activeSource.value);

const rawSourceState = computed(() => {
  if (transitioning.value) return 'starting';
  // CD: ejecting disc
  if (activeSource.value === 'cd' && sourceState.value === 'waiting' &&
      metadata.value?.ejecting) {
    return 'ejecting';
  }
  // CD: disc present but TOC or metadata not yet attached.
  // disc_id is emitted by _build_metadata only once `_current_disc` is set
  // — true on either MusicBrainz success OR fallback. Its absence covers
  // the activation window where the TOC has been read but the lookup
  // hasn't completed; once `_current_disc` is populated has_disc flips
  // the source state to ACTIVE and we leave 'loading_disc' anyway.
  if (activeSource.value === 'cd' && sourceState.value === 'waiting' &&
      metadata.value?.disc_present &&
      (!metadata.value?.cache_ready || !metadata.value?.disc_id)) {
    return 'loading_disc';
  }
  // CD: no drive connected (active source but hardware missing)
  if (activeSource.value === 'cd' && sourceState.value === 'waiting' &&
      metadata.value?.drive_connected === false) {
    return 'no_drive';
  }
  return sourceState.value;
});

// Minimum display time for "starting" state (prevents flash before "loading_disc")
const STARTING_MIN_MS = 2000;
const currentSourceState = ref(rawSourceState.value);
const timer = useTimer();
let startingEnteredAt = null;
let startingTimer = null;

watch(rawSourceState, (newState, oldState) => {
  timer.clear(startingTimer);

  if (newState === 'starting') {
    startingEnteredAt = Date.now();
    currentSourceState.value = 'starting';
    return;
  }

  // Leaving "starting" — enforce minimum display time
  if (oldState === 'starting' && startingEnteredAt) {
    const remaining = STARTING_MIN_MS - (Date.now() - startingEnteredAt);
    if (remaining > 0) {
      startingTimer = timer.setTimeout(() => {
        currentSourceState.value = rawSourceState.value;
        startingEnteredAt = null;
      }, remaining);
      return;
    }
  }

  startingEnteredAt = null;
  currentSourceState.value = newState;
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

// Key for transitions - includes state for source status to animate between states
const contentKey = computed(() => {
  if (shouldShowSourceStatus.value) {
    return `${activeSource.value}-${currentSourceState.value}-${!!currentDeviceName.value}`;
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

/* Source status: naturally centered */
.source-status-container {
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