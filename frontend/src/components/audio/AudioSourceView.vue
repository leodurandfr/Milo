<template>
  <div class="audio-source-view">
    <Transition name="audio-content" appear>
      <div v-if="lyricsStore.isOpen" key="lyrics" class="audio-source-slot lyrics-slot">
        <LyricsView />
      </div>

      <div v-else-if="shouldShowSpotify" :key="contentKey" class="audio-source-slot">
        <SpotifySource />
      </div>

      <div v-else-if="shouldShowRadio" :key="contentKey" class="audio-source-slot">
        <RadioSource />
      </div>

      <div v-else-if="shouldShowPodcast" :key="contentKey" class="audio-source-slot">
        <PodcastSource />
      </div>

      <div v-else-if="shouldShowCD" :key="contentKey" class="audio-source-slot">
        <CDSource />
      </div>

      <div v-else-if="shouldShowMusicLibrary" :key="contentKey" class="audio-source-slot">
        <MusicLibrarySource />
      </div>

      <div v-else-if="shouldShowAirPlay" :key="contentKey" class="audio-source-slot">
        <AirPlaySource />
      </div>

      <div v-else-if="shouldShowDLNA" :key="contentKey" class="audio-source-slot">
        <DLNASource />
      </div>

      <div v-else-if="shouldShowQobuz" :key="contentKey" class="audio-source-slot">
        <QobuzSource />
      </div>

      <div v-else-if="shouldShowSourceStatus" :key="contentKey" class="audio-source-slot source-status-container">
        <AudioSourceStatus :source-type="currentSourceType" :source-state="currentSourceState"
          :device-name="currentDeviceName" :is-disconnecting="isDisconnecting"
          :account-connected="qobuzAccountConnected" @disconnect="handleDisconnect" @connect="handleConnect" />
      </div>

    </Transition>
  </div>
</template>

<script setup>
import { computed, ref, watch, inject, defineAsyncComponent } from 'vue';
import { useTimer } from '@/composables/useTimer';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useLyricsStore } from '@/stores/lyricsStore';
import { useRichDisplay } from '@/composables/useRichDisplay';

const LyricsView = defineAsyncComponent(() =>
  import('../lyrics/LyricsView.vue')
);
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
const MusicLibrarySource = defineAsyncComponent(() =>
  import('../music-library/MusicLibrarySource.vue')
);
const DLNASource = defineAsyncComponent(() =>
  import('../dlna/DLNASource.vue')
);
const QobuzSource = defineAsyncComponent(() =>
  import('../qobuz/QobuzSource.vue')
);
import AudioSourceStatus from './AudioSourceStatus.vue';

const unifiedStore = useUnifiedAudioStore();
const lyricsStore = useLyricsStore();

const activeSource = computed(() => unifiedStore.systemState.active_source);
const sourceState = computed(() => unifiedStore.systemState.source_state);
const transitioning = computed(() => unifiedStore.systemState.transitioning);
const metadata = computed(() => unifiedStore.systemState.metadata);

// === DISCONNECT LOGIC ===
const isDisconnecting = computed(() => unifiedStore.isDisconnecting(activeSource.value));

function handleDisconnect() {
  unifiedStore.disconnectSource(activeSource.value);
}

// Qobuz login state rides the broadcast metadata (account_authenticated). Only an
// explicit false — the proxy confirming no account — arms the "connect account"
// CTA; an absent field (pre-first-poll, or any non-Qobuz source) reads as
// connected so the card never flashes the CTA before status arrives.
const qobuzAccountConnected = computed(() => {
  if (activeSource.value !== 'qobuz') return true;
  return metadata.value?.account_authenticated !== false;
});

// The CTA opens the Qobuz account settings screen for the one-time login.
const openSettings = inject('openSettings');
function handleConnect() {
  openSettings?.('qobuz');
}

// === DISPLAY DECISION ===
// `richSource` (the active source resolved to a rich full-screen view, or null
// → AudioSourceStatus fallback) is the ONE rule that decides which component
// mounts below. It lives in useRichDisplay so MainView's logo visibility reads
// the exact same decision and can't drift out of sync — see the composable.
const { richSource } = useRichDisplay();

const shouldShowSpotify = computed(() => richSource.value === 'spotify');
const shouldShowRadio = computed(() => richSource.value === 'radio');
const shouldShowPodcast = computed(() => richSource.value === 'podcast');
const shouldShowCD = computed(() => richSource.value === 'cd');
const shouldShowMusicLibrary = computed(() => richSource.value === 'music_library');
const shouldShowAirPlay = computed(() => richSource.value === 'airplay');
const shouldShowDLNA = computed(() => richSource.value === 'dlna');
const shouldShowQobuz = computed(() => richSource.value === 'qobuz');

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

// Minimum display time for "starting" state: a short anti-flash buffer so a
// fast backend transition (e.g. CD's quick starting -> loading_disc) doesn't
// flicker the card. Kept just above the flash-perception threshold so fast
// sources feel near-instant instead of being padded to a uniform delay.
const STARTING_MIN_MS = 500;
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
      return meta.client_names || [];
    case 'airplay':
      return meta.client_name || '';
    case 'dlna':
      // Passive receiver: client_name is the static "DLNA" label the player's
      // source bar needs, not a controller identity — UPnP does not expose one.
      // Keep the status card generic (handled in AudioSourceStatus).
      return '';
    case 'qobuz':
      // Passive receiver: the proxy exposes no controller identity, only the
      // speaker name. Keep the status card generic (handled in AudioSourceStatus).
      return '';
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

.audio-source-slot {
  position: absolute;
  inset: 0;
  display: grid;
  /* Clamp the single row to the slot's fixed height (minmax min:0, not auto) so a
     source whose content is taller than the viewport doesn't grow the row — which
     would make the child's height:100% resolve to content height, leaving
     .audio-source-layout's overflow-y:auto nothing to scroll and clipping the
     overflow under #app{overflow:hidden}. First surfaced by Music Library's long
     track lists; the row now stays viewport-height and the layout scrolls. */
  grid-template-rows: minmax(0, 1fr);
}

.source-status-container {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-05);
}

/* === TRANSITIONS WITH FORCED DIRECTIONS === */

/* The `audio-content` swap itself lives in design-system.css, next to the other
   named transitions: the gallery's source pages replay it, and a second copy is
   a second thing to keep true. Only the lyrics exception is local — being
   scoped, it outranks the shared rules it overrides. */

/* Lyrics fades in/out in place instead of using the generic slide — the spring
   transform above would drag the blurred backdrop along with it. Both
   directions are a plain opacity fade (the backdrop's own progressive reveal
   is its own separate transition, see .lyrics-bg in LyricsView.vue). */
.lyrics-slot.audio-content-enter-active,
.lyrics-slot.audio-content-leave-active {
  transition: opacity var(--transition-in-out);
}
.lyrics-slot.audio-content-enter-from,
.lyrics-slot.audio-content-leave-to {
  opacity: 0;
  transform: none;
}
.lyrics-slot.audio-content-enter-to,
.lyrics-slot.audio-content-leave-from {
  opacity: 1;
  transform: none;
}

</style>