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

      <div v-else-if="shouldShowTidal" :key="contentKey" class="audio-source-slot">
        <TidalSource />
      </div>

      <div v-else-if="shouldShowBluetooth" :key="contentKey" class="audio-source-slot">
        <BluetoothSource />
      </div>

      <div v-else-if="shouldShowSourceStatus" :key="contentKey" class="audio-source-slot source-status-container">
        <AudioSourceStatus :source-type="currentSourceType" :display-state="displayState"
          :unavailable-reason="unavailableReason" :device-name="currentDeviceName"
          :is-disconnecting="isDisconnecting" @disconnect="handleDisconnect" @connect="handleConnect"
          @retry="handleRetry" @open-network-settings="handleOpenNetworkSettings" />
      </div>

    </Transition>
  </div>
</template>

<script setup>
import { computed, inject, defineAsyncComponent } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useLyricsStore } from '@/stores/lyricsStore';
import { useRichDisplay } from '@/composables/useRichDisplay';
import { useSourceStatusDisplay } from '@/composables/useSourceStatusDisplay';

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
const TidalSource = defineAsyncComponent(() =>
  import('../tidal/TidalSource.vue')
);
const BluetoothSource = defineAsyncComponent(() =>
  import('../bluetooth/BluetoothSource.vue')
);
import AudioSourceStatus from './AudioSourceStatus.vue';

const unifiedStore = useUnifiedAudioStore();
const lyricsStore = useLyricsStore();

const activeSource = computed(() => unifiedStore.systemState.active_source);
const transitioning = computed(() => unifiedStore.systemState.transitioning);
const metadata = computed(() => unifiedStore.systemState.metadata);

// === DISCONNECT LOGIC ===
const isDisconnecting = computed(() => unifiedStore.isDisconnecting(activeSource.value));

function handleDisconnect() {
  unifiedStore.disconnectSource(activeSource.value);
}

// The retry a failed transition leaves available: the source stays selected in
// ERROR, so re-selecting it is what re-runs the start the state machine gave up
// on (the guard in state.py lets the same source through when it is errored).
function handleRetry() {
  unifiedStore.changeSource(activeSource.value);
}

// The two CTAs a missing prerequisite offers, both of them a settings screen:
// the one-time Qobuz login, and the Wi-Fi/Ethernet setup.
const openSettings = inject('openSettings');
function handleConnect() {
  openSettings?.('qobuz');
}
function handleOpenNetworkSettings() {
  openSettings?.('network');
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
const shouldShowTidal = computed(() => richSource.value === 'tidal');
const shouldShowBluetooth = computed(() => richSource.value === 'bluetooth');

const shouldShowSourceStatus = computed(() => {
  if (activeSource.value === 'none') return false;  // nothing active (incl. deactivation)
  if (transitioning.value) return true;             // status card during transitions
  return richSource.value === null;                 // active source without a rich view
});

// === PROPERTIES FOR SOURCE STATUS ===
const currentSourceType = computed(() => activeSource.value);

// The card's vocabulary — the backend enum plus CD's two transient screens,
// through the anti-flash floor — and what stops the source working, if
// anything. Derived in one place so the gallery's source pages document the
// same derivation the app performs.
const { displayState, unavailableReason } = useSourceStatusDisplay();

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
      // Passive receiver: client_name is the player source bar's label — the
      // media server the audio comes from, or the static "DLNA" — never a
      // controller identity, which UPnP does not expose. And an idle renderer
      // has no server either, so there is nothing to name on the status card.
      // Keep it generic (handled in AudioSourceStatus).
      return '';
    case 'qobuz':
      // Passive receiver: the proxy exposes no controller identity, only the
      // speaker name. Keep the status card generic (handled in AudioSourceStatus).
      return '';
    default:
      return '';
  }
});

// Key for transitions - includes state for source status to animate between states.
// The reason is in it because it changes while the state does not: unplugging the
// CD drive leaves READY on the wire and only rewrites the card's second line, so
// without it the "no drive" screen cut in instead of crossing over.
const contentKey = computed(() => {
  if (shouldShowSourceStatus.value) {
    return `${activeSource.value}-${displayState.value}-${unavailableReason.value}-${!!currentDeviceName.value}`;
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
  /* Same clamp on the column, for the same reason in the other axis: an auto
     track sizes to the source's max-content, so anything unbreakable inside one
     (a nowrap header title) widened the whole app instead of overflowing itself. */
  grid-template-columns: minmax(0, 1fr);
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