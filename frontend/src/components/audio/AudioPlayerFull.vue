<!-- AudioPlayerFull.vue - Full-screen player for Spotify, AirPlay, and CD -->
<template>
  <div class="connect-player">
    <div class="now-playing">
      <!-- Left side: Cover image with CSS staggering -->
      <div class="artwork-section stagger-1" :class="{ 'art-collapsed': hideContent }">
        <div class="artwork-container">
          <!-- Background blur -->
          <div class="artwork-blur"
            :style="{ backgroundImage: shownArtwork ? `url(${shownArtwork})` : 'none' }">
          </div>

          <!-- Main cover art. The fallback is not decoration: Bluetooth can
               never carry a cover over the link (AVRCP puts images behind an
               OBEX channel BlueZ gives no client for), so when the lookup that
               replaces it finds nothing, this is what the slot shows instead of
               a blank square reading as a failed image. Which of the two it is
               comes from the shared helper, not from here — the screensaver
               resolves it the same way, and a fallback chosen per view is how
               the two came to disagree in the first place. -->
          <div class="artwork" :class="{ 'artwork-pending': artworkPending }">
            <img v-if="shownArtwork" :src="shownArtwork"
              alt="Artwork" />
            <img v-else-if="fallback.kind === 'image'" :src="fallback.src"
              alt="" class="artwork-placeholder" />
            <div v-else class="artwork-fallback">
              <AppIcon :name="source" :size="112" />
            </div>

            <!-- Held over the outgoing cover until the incoming one is decoded,
                 so a track change never flashes the fallback glyph between two
                 covers. -->
            <Transition name="artwork-veil">
              <div v-if="artworkPending" class="artwork-veil">
                <LoadingSpinner :size="48" />
              </div>
            </Transition>
          </div>

          <!-- Decodes the incoming cover off-screen; @load is what promotes it —
               or rejects it, the size rule living in the composable so this view
               and the screensaver cannot reach opposite verdicts. -->
          <img v-if="preloadArtwork" :src="preloadArtwork" alt="" class="artwork-preload"
            @load="settleFromLoad" @error="settleFromError" />
        </div>
      </div>

      <!-- Right side: Info and controls with CSS staggering.
           Keyed on the screensaver reveal nonce so dismissing the screensaver
           remounts just this column and replays its stagger — the artwork column
           (left) stays put, giving a seamless cover-to-player continuity. -->
      <div class="content-section stagger-2" :key="revealNonce">
        <!-- Action buttons (used by CD for eject/tracklist) -->
        <slot name="action-buttons" />

        <!-- Content: player info or replacement (e.g., CD tracklist) -->
        <Transition name="player-swap" mode="out-in">
          <div v-if="!hideContent" key="player-info" class="player-info">
            <div class="track-info" :class="{ 'no-controls': !showControls }">
              <h1 class="track-title heading-1">{{ persistentMetadata.title || t('status.unknownTitle') }}</h1>
              <p class="track-artist heading-2">{{ persistentMetadata.artist || t('status.unknownArtist') }}</p>
            </div>
            <div class="controls-section">
              <template v-if="showControls">
                <div class="progress-wrapper">
                  <ProgressBar :currentPosition="currentPosition" :duration="duration"
                    :progressPercentage="progressPercentage" :isReady="isPositionInitialized"
                    :interactive="seekable" animateIn @seek="seekTo" />
                </div>
                <div class="controls-wrapper">
                  <PlaybackControls :isPlaying="isPlaying" :isBuffering="isBuffering" :hasNext="hasNext"
                    @play-pause="togglePlayPause" @previous="previousTrack" @next="nextTrack" />
                </div>
              </template>
              <template v-else>
                <div v-if="showProgress" class="progress-wrapper">
                  <ProgressBar :currentPosition="currentPosition" :duration="duration"
                    :progressPercentage="progressPercentage" :isReady="isPositionInitialized"
                    :interactive="false" animateIn />
                </div>
                <div class="source-bar">
                  <AppIcon :name="source" :size="40" />
                  <span class="source-bar-name heading-4">{{ sourceBarName }}</span>
                </div>
              </template>
            </div>
          </div>
          <div v-else key="content-replace" class="content-replace">
            <slot name="content-replace" />
          </div>
        </Transition>
      </div>
    </div>

    <!-- No error branch here on purpose: `full_state.error` is only ever set
         alongside SourceState.ERROR, and useRichDisplay refuses a rich display
         in that state before it looks at the source at all — so this player is
         never mounted with a message to show. The status card draws it. -->
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useSourceProgress } from '@/composables/useSourceProgress';
import { useScreensaverRevealNonce } from '@/composables/useScreensaverReveal';
import { isSourceBuffering } from '@/utils/playbackBuffering';
import { useI18n } from '@/services/i18n';
import { AUDIO_SOURCE_LABEL_KEYS } from '@/constants/audioSources';

import { useArtworkTransition } from '@/composables/useArtworkTransition';
import { nowPlayingArtwork, artworkFallback } from '@/utils/nowPlayingArtwork';
import { nowPlayingSnapshot } from '@/utils/nowPlayingMetadata';

import PlaybackControls from './PlaybackControls.vue';
import ProgressBar from './ProgressBar.vue';
import AppIcon from '@/components/ui/AppIcon.vue';
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue';

const props = defineProps({
  source: {
    type: String,
    required: true
  },
  showControls: {
    type: Boolean,
    default: true
  },
  // Receiver-controlled sources (showControls=false) that still report
  // position/duration: adds a read-only bar above the source bar. DLNA and
  // Qobuz opt in — they broadcast position only every 30 s, and
  // useSourceProgress interpolates between corrections. AirPlay passes false
  // on purpose: nothing on that channel reports that the sender paused, so an
  // interpolated bar runs on through a paused track. Off by default for the
  // controlled sources, which draw their own bar next to the transport.
  showProgress: {
    type: Boolean,
    default: false
  },
  // Whether the bar drawn next to the transport accepts a scrub. Default true:
  // every controlled source but one can seek. Tidal cannot — its controller
  // protocol has no seek command at all (the daemon seeks internally, but
  // exposes nothing for it), so offering the gesture would just drop taps.
  // Only consulted when showControls is set; the read-only bar the receiver
  // sources draw is never interactive.
  seekable: {
    type: Boolean,
    default: true
  },
  hideContent: {
    type: Boolean,
    default: false
  },
  // Default true: sources with no "last track" concept (Spotify, AirPlay) stay unaffected.
  hasNext: {
    type: Boolean,
    default: true
  }
});

const { t } = useI18n();
const unifiedStore = useUnifiedAudioStore();
const { currentPosition, duration, progressPercentage, seekTo, isPositionInitialized } = useSourceProgress(props.source);

// Remount the right column (title/controls) to replay its entrance when the
// screensaver is dismissed — the artwork column is left untouched on purpose.
const revealNonce = useScreensaverRevealNonce();

// Playback controls — sendCommand swallows + logs errors via the store.
function sendSourceCommand(command) {
  return unifiedStore.sendCommand(props.source, command);
}

function togglePlayPause() {
  sendSourceCommand(unifiedStore.systemState.metadata?.is_playing ? 'pause' : 'resume');
}

function previousTrack() {
  sendSourceCommand('prev');
}

function nextTrack() {
  sendSourceCommand('next');
}

// === METADATA PERSISTENCE ===
const lastValidMetadata = ref({
  title: '',
  artist: '',
  album_art_url: ''
});

// Cache last valid metadata so the UI doesn't blank out during brief gaps.
// What counts as worth keeping is per-source and lives in the util — CD is the
// one source that reaches this player with no artist.
watch(
  () => unifiedStore.systemState.metadata,
  (currentMetadata) => {
    const snapshot = nowPlayingSnapshot(props.source, currentMetadata);
    if (snapshot) lastValidMetadata.value = snapshot;
  },
  { immediate: true }
);

const persistentMetadata = computed(() => lastValidMetadata.value);

// Real-time playback state (not persisted)
const isPlaying = computed(() => unifiedStore.systemState.metadata?.is_playing || false);

const isBuffering = computed(() =>
  isSourceBuffering(props.source, unifiedStore.systemState.metadata)
);


// Who is sending, when the channel says so: AirPlay's sender, DLNA's media
// server. Nothing identifies the sender on the other receiver channels — UPnP
// never names the control point, the Qobuz proxy only knows the speaker — so
// the answer there is the source itself, read from the same key the status card
// and the dock use, never a label a backend hardcoded in one language.
const sourceBarName = computed(
  () => unifiedStore.systemState.metadata?.client_name
    || t(AUDIO_SOURCE_LABEL_KEYS[props.source])
);

// === ARTWORK TRANSITION ===
// Which cover this source shows is decided in one place, shared with
// useScreensaver so the two views can never disagree — see the util.
const targetArtwork = computed(() => nowPlayingArtwork(persistentMetadata.value));
// What the slot shows with no cover at all — a bundled placeholder for the
// sources that ship one, this source's own glyph otherwise.
const fallback = computed(() => artworkFallback(props.source));

// Holding the outgoing cover under a veil while the next one decodes is shared
// with the screensaver — the two are superimposed during its leave crossfade,
// so the transition has to behave identically in both.
const trackKey = computed(
  () => `${persistentMetadata.value.title}|${persistentMetadata.value.artist}`
);
const { shownArtwork, preloadArtwork, artworkPending, settleFromLoad, settleFromError } =
  useArtworkTransition(targetArtwork, trackKey);



</script>

<style scoped>
/* === SIMPLE AND NATURAL STAGGERING === */

/* Initial states: all elements are hidden */
.stagger-1,
.stagger-2 {
  opacity: 0;
  transform: translateY(var(--space-07));
}

/* Animation with two separate effects */
.connect-player .stagger-1,
.connect-player .stagger-2 {
  animation:
    stagger-transform var(--transition-spring) forwards,
    stagger-opacity 0.4s ease forwards;
}

/* Simple staggered delays */
.connect-player .stagger-1 { animation-delay: 0ms; }
.connect-player .stagger-2 { animation-delay: 0ms; }

/* Spring animation for transform */
@keyframes stagger-transform {
  to {
    transform: none;
  }
}

/* Ease animation for opacity */
@keyframes stagger-opacity {
  to {
    opacity: 1;
  }
}

/* === COMPONENT STYLES === */
.connect-player {
  width: 100%;
  height: 100%;
  overflow: hidden;
  position: relative;
}

.now-playing {
  display: flex;
  height: 100%;
  padding: var(--space-05) var(--space-06) var(--space-05) var(--space-05);
  gap: var(--space-06);
  background: var(--color-background-neutral);
}

/* Artwork */
.artwork-section {
  flex-shrink: 0;
  aspect-ratio: 1;
  order: 1;
  z-index: 2;
  pointer-events: none;
}

/* Content Section */
.content-section {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  order: 2;
  z-index: 1;
}

/* Player info (track-info + controls) */
.player-info {
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  min-width: 0;
  min-height: 0;
}

/* Content replacement (e.g., CD tracklist) */
.content-replace {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

/* === PLAYER SWAP TRANSITION === */
/* Leave: quick fade out */
.player-swap-leave-active {
  transition: opacity var(--transition-fast-leave);
}

.player-swap-leave-to {
  opacity: 0;
}

/* Enter: no parent animation — children stagger themselves */

/* Stagger children on mount (initial load + re-enter after swap) */
.player-info > .track-info,
.player-info > .controls-section {
  opacity: 0;
  transform: translateY(var(--space-05));
  animation:
    stagger-transform var(--transition-spring) forwards,
    stagger-opacity 0.4s ease forwards;
}

.player-info > .track-info { animation-delay: 0ms; }
.player-info > .controls-section { animation-delay: 100ms; }

/* Container for the two stacked cover arts */
.artwork-container {
  position: relative;
  width: 100%;
  height: 100%;
}

/* Background cover art with blur */
.artwork-blur {
  position: absolute;
  top: -20px;
  left: -20px;
  right: -20px;
  bottom: -20px;
  z-index: 2;
  background-size: cover;
  background-position: center;
  filter: blur(var(--blur-04)) saturate(1.5);
  transform: scale(1.1) translateZ(0);
  opacity: .25;
  will-change: transform;
  -webkit-backface-visibility: hidden;
  backface-visibility: hidden;
  contain: strict;
}

/* Main cover art with border radius */
.artwork {
  position: relative;
  z-index: 3;
  width: 100%;
  height: 100%;
  border-radius: var(--radius-04);
  overflow: hidden;
  box-shadow: var(--shadow-artwork);
  pointer-events: none;
}

.artwork img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.artwork-fallback {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--color-background-strong);
  color: var(--color-text-light);
}

/* Bundled placeholder. It is transparent by design — the same file sits on
   cards of two different colours elsewhere — so it needs the ground the glyph
   fallback gets, or the blurred backdrop shows through it. */
.artwork-placeholder {
  background: var(--color-background-strong);
}

/* Held cover while the next one decodes. The scale is not decoration: a blur
   samples past the element's edge, and without it the rounded corners show a
   translucent halo against the player background. */
.artwork > img,
.artwork > .artwork-fallback {
  transition:
    filter var(--transition-medium),
    transform var(--transition-medium);
}

.artwork-pending > img,
.artwork-pending > .artwork-fallback {
  filter: blur(var(--blur-02));
  transform: scale(1.06);
}

.artwork-veil {
  position: absolute;
  inset: 0;
  z-index: 4;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--color-background-contrast-32);
  /* The spinner's SVG paints with currentColor, and it sits on a darkened cover
     — not on the player background — so it takes the contrast token rather than
     inheriting the page text colour. Full contrast, not -50: the blades already
     animate down to 0.16 opacity, and halving that again loses them over a
     bright cover. */
  color: var(--color-text-contrast);
}

.artwork-veil-enter-active,
.artwork-veil-leave-active {
  transition: opacity var(--transition-medium);
}

.artwork-veil-enter-from,
.artwork-veil-leave-to {
  opacity: 0;
}

/* Decodes the incoming cover out of sight. Deliberately not `display: none`,
   which lets a browser skip the fetch — and the load event it fires is the
   whole mechanism. */
.artwork-preload {
  position: absolute;
  width: 1px;
  height: 1px;
  opacity: 0;
  pointer-events: none;
}

.track-info {
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: center;
  text-align: center;
  gap: var(--space-03);
  min-width: 0;
  padding-top: var(--space-06);
}

.track-info.no-controls {
  padding-top: 0;
}

.controls-section {
  display: flex;
  flex-direction: column;
  gap: var(--space-05);
}

/* Reserve the progress bar's row height even while it's hidden (idle CD) so the
   centered track-info doesn't shift when the bar mounts on play. Matches the
   bar's flex-row height, which the .time line-height (--line-height-mono)
   dominates over the 8px track. */
.progress-wrapper {
  min-height: var(--line-height-mono);
}

.track-title {
  color: var(--color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.track-artist {
  color: var(--color-text-light);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* Source bar (AirPlay device info) */
.source-bar {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-03);
  padding-bottom: var(--space-06);
}

.source-bar-name {
  color: var(--color-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

@media (max-aspect-ratio: 4/3) {
  .now-playing {
    padding-left: var(--space-05);
    padding-right: var(--space-05);
    padding-top: max(var(--space-05), env(safe-area-inset-top, 0px));
    padding-bottom: max(var(--space-06), env(safe-area-inset-bottom, 0px));

    flex-direction: column;
    gap: 0;
  }

  .content-replace {
    margin-bottom: calc(-1 * max(var(--space-06), env(safe-area-inset-bottom, 0px)));
  }

  .controls-section {
    margin-bottom: calc(env(safe-area-inset-bottom, 0px));
  }

  .content-section {
    z-index: auto;
  }

  .connect-player .content-section {
    transform: none;
    opacity: 1;
    animation: none;
  }

  /* Collapse album art when tracklist is open, keeping a strip for action buttons */
  .artwork-section {
    transition: margin-top 400ms var(--easeInOutCubic);
  }

  .artwork-section.art-collapsed {
    /* Buttons absolute top (from connect-player) minus artwork offset (from now-playing padding) */
    --btn-top: calc(max(var(--space-05), env(safe-area-inset-top, 0px)) + var(--space-04));
    --art-top: max(var(--space-05), env(safe-area-inset-top, 0px));
    --btn-height: 40px;
    --art-visible: calc(var(--btn-top) - var(--art-top) + var(--btn-height) + var(--space-04));
    margin-top: calc(-100vw + 2 * var(--space-05) + var(--art-visible));
  }

  .artwork {
    border-radius: var(--radius-07);
  }
  .artwork-blur {
    transform: scale(1) translateZ(0);
  }

  .track-info {
    padding: var(--space-06) 0 var(--space-03) 0;
  }

  .track-info.no-controls {
    padding: 0;
  }
}
</style>
