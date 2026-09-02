<template>
  <Transition name="screensaver">
    <div v-if="isVisible" class="screensaver-overlay" @pointerdown.stop="handleClose">
    <!-- ===== MEDIA MODE ===== -->
    <template v-if="mode === 'media'">
      <!-- Full-screen blurred background -->
      <div class="artwork-background">
        <img v-if="shownArtwork" :src="shownArtwork" alt="" class="background-image" />
        <div v-else-if="stationAvatarSvg" v-html="stationAvatarSvg" class="background-image" />
      </div>

      <!-- Centered blur halo. Heavy blur + 0.12 opacity make the font of the
           station avatar effectively invisible, so encoding it as a data URL for
           CSS background-image is fine here (no font-cascade requirement). -->
      <div class="artwork-blur"
        :style="{ backgroundImage: haloUrl ? `url(${haloUrl})` : 'none' }">
      </div>

      <!-- Main content: full-width horizontal layout -->
      <div class="now-playing-screensaver">
        <!-- Left: Artwork -->
        <div class="artwork-section stagger-1" :class="{ 'artwork-leave-rise': artworkRises }">
          <div class="artwork-container">
            <div class="artwork" :class="{ 'artwork-pending': artworkPending }">
              <img v-if="shownArtwork" :src="shownArtwork" :alt="title" />
              <div v-else-if="stationAvatarSvg" v-html="stationAvatarSvg" :aria-label="title" class="artwork-fallback" />
              <img v-else-if="fallback.kind === 'image'" :src="fallback.src" alt="" class="artwork-placeholder" />
              <div v-else-if="sourceType" class="artwork-glyph"><AppIcon :name="sourceType" :size="112" /></div>

              <!-- Held over the outgoing cover until the incoming one decodes,
                   so a track change never flashes the generated avatar between
                   two covers. Same veil as AudioPlayerFull. -->
              <Transition name="artwork-veil">
                <div v-if="artworkPending" class="artwork-veil">
                  <LoadingSpinner :size="48" />
                </div>
              </Transition>
            </div>

            <!-- Decodes the incoming cover off-screen; its load is what promotes
                 it, or rejects it — the too-small check lives in the composable
                 so this view and the player cannot reach opposite verdicts. -->
            <img v-if="preloadArtwork" :src="preloadArtwork" alt="" class="artwork-preload"
              @load="settleFromLoad" @error="settleFromError" />
          </div>
        </div>

        <!-- Right: Title + subtitle centered, station bar at bottom -->
        <div class="content-section stagger-2">
          <div class="track-info stagger-3">
            <h1 class="track-title heading-1">{{ title }}</h1>
            <p v-if="subtitle" class="track-subtitle" :class="useMonoSubtitle ? 'text-mono-medium' : 'heading-2'">{{ subtitle }}</p>
          </div>

          <div v-if="showBottomBar" class="station-bar stagger-4">
            <img v-if="stationFavicon" :src="stationFavicon" alt="" class="station-favicon" />
            <AppIcon v-else-if="stationIcon" :name="stationIcon" :size="40" class="station-icon" />
            <span class="station-name heading-4">{{ stationName }}</span>
          </div>

          <div v-if="progress" class="progress-section stagger-4"
            :class="{ 'progress-leave-converge': progressConverges }">
            <ProgressBar
              :current-position="progress.currentPosition"
              :duration="progress.duration"
              :progress-percentage="progress.progressPercentage"
              :is-ready="progress.isReady"
              :interactive="false"
              variant="dark"
              animate-in />
          </div>
        </div>
      </div>
    </template>

    <!-- ===== SIMPLE MODE ===== -->
    <template v-else>
      <div class="simple-screensaver stagger-1">
        <AppIcon :name="sourceType" size="medium" :class="{ 'simple-icon-invert': sourceType === 'mac' }" />
        <p class="simple-status heading-1">{{ title }}</p>
        <h1 class="simple-device-name heading-1">{{ subtitle }}</h1>
      </div>
    </template>
    </div>
  </Transition>
</template>

<script setup>
import { computed } from 'vue';
import AppIcon from '@/components/ui/AppIcon.vue';
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue';
import ProgressBar from './ProgressBar.vue';
import { useArtworkTransition } from '@/composables/useArtworkTransition';
import { generateStationAvatarSvg } from '@/utils/stationAvatar';
import { artworkFallback } from '@/utils/nowPlayingArtwork';
import { ALL_AUDIO_SOURCES } from '@/constants/audioSources';

const props = defineProps({
  isVisible: {
    type: Boolean,
    required: true
  },
  progressConverges: {
    type: Boolean,
    default: false
  },
  artworkRises: {
    type: Boolean,
    default: false
  },
  mode: {
    type: String,
    default: 'media',
    validator: (value) => ['media', 'simple'].includes(value)
  },
  // The active source id. In simple mode it is both the AppIcon name and the
  // Mac test; in media mode it is what resolves the no-cover fallback.
  sourceType: {
    type: String,
    default: null,
    validator: (value) => value === null || ALL_AUDIO_SOURCES.includes(value)
  },
  artwork: {
    type: String,
    default: null
  },
  // Two meanings, one prop: the track title in media mode, the status line
  // ("Connected to") in simple mode — where `subtitle` carries the device name.
  title: {
    type: String,
    required: true
  },
  subtitle: {
    type: String,
    default: null
  },
  stationFavicon: {
    type: String,
    default: null
  },
  // Bottom-bar glyph (AppIcon name) used when there is no favicon URL — e.g.
  // AirPlay shows the AirPlay icon + sender name where radio shows the favicon.
  stationIcon: {
    type: String,
    default: null
  },
  stationName: {
    type: String,
    default: null
  },
  useMonoSubtitle: {
    type: Boolean,
    default: false
  },
  progress: {
    type: Object,
    default: null
  }
});

const emit = defineEmits(['close']);

// The same held-cover transition AudioPlayerFull runs, from the same composable:
// this view crossfades into that one on dismiss, so a transition that behaved
// differently here would show up exactly when the two are superimposed.
const artworkTarget = computed(() => props.artwork || '');
const trackKey = computed(() => `${props.title}|${props.subtitle}`);
const { shownArtwork, preloadArtwork, artworkPending, settleFromLoad, settleFromError } =
  useArtworkTransition(artworkTarget, trackKey);

// What fills the slot with no cover — resolved by the shared helper, exactly as
// AudioPlayerFull resolves it, so the two views cannot answer differently for
// the same silence.
const fallback = computed(() => artworkFallback(props.sourceType));

// The generated station avatar, and only for radio: it is the station's
// identity, not a stand-in. Reading `title` as well as `stationName` is
// deliberate — a station with no recognised track puts its own name in `title`
// — but the helper is what decides this branch is reachable at all, which is
// what stops a DLNA renderer being announced as the word "DLNA" in a tile.
const stationAvatarSvg = computed(() => {
  if (fallback.value.kind !== 'avatar') return '';
  const name = props.stationName || props.title;
  return name ? generateStationAvatarSvg(name) : '';
});
// CSS background-image needs a URL, not raw markup — encode the inline SVG
// just for the halo. Safe here because the heavy blur + low opacity hide any
// font-cascade difference.
const haloUrl = computed(() => {
  if (shownArtwork.value) return shownArtwork.value;
  if (stationAvatarSvg.value) return `data:image/svg+xml;utf8,${encodeURIComponent(stationAvatarSvg.value)}`;
  return null;
});
// The name alone gates the bar: a glyph with nothing to label is not a bar.
// Deliberate — DLNA renderers routinely send no client name, and useScreensaver
// passes `stationIcon` unconditionally for them, expecting the bar to hide.
const showBottomBar = computed(() => !!props.stationName);

// Emit immediately; the parent flips isVisible and <Transition> plays the leave
// animation. So a programmatic close (playback paused/stopped) fades out exactly
// like a user dismiss — and a resume mid-fade cancels the leave automatically.
function handleClose() {
  emit('close');
}
</script>

<style scoped>
.screensaver-overlay {
  /* Distance the progress bar travels up on leave to land exactly where
     AudioPlayerFull shows it (above the play controls): controls row height
     (play-pause 90px + its vertical padding) + controls-section gap − the bar's
     resting padding-bottom. The title travels half of it (it centers in the
     freed space). Bar is bottom-anchored in the player, so this holds for CD's
     action-buttons layout too. */
  --screensaver-leave-dy: calc(90px + 2 * var(--space-01) + var(--space-05) - var(--space-06));
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: #000000;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  z-index: 7000;
  contain: layout paint;
}

/* Enter/leave via <Transition>: the leave animation plays for every close —
   user dismiss AND programmatic hide (playback paused/stopped). */
.screensaver-enter-active {
  animation: fadeIn 400ms ease-out;
}

.screensaver-leave-active {
  animation: fadeOut 300ms ease-out forwards;
}

/* Artwork rises + fades on leave only for sources whose revealed view has no
   matching cover (Radio/Podcast/Music Library → AudioSourceLayout). The
   AudioPlayerFull sources keep it fixed for cover continuity, so this class is
   not applied there. */
.screensaver-overlay.screensaver-leave-active .artwork-section.artwork-leave-rise {
  animation: screensaverLeaveUp 300ms ease-out forwards;
}

/* On leave each element lifts up as it fades to finish exactly where its
   AudioPlayerFull counterpart sits, so the crossfade reads as one set of
   elements repositioning rather than two overlapping layouts. The artwork and
   the station/source bar are NOT translated — they already sit at the same spot
   in both views, so they only fade in place (via the overlay opacity) and stay
   superimposed. Rules are more specific than the .stagger-* enter rule so they
   win; the explicit `from` keeps opacity from snapping to the stagger base (0)
   when the enter animation is replaced. */
.screensaver-overlay.screensaver-leave-active .track-info {
  animation: screensaverLeaveTitleUp 300ms ease-out forwards;
}

/* Progress: default gentle rise (CD, podcast, music_library …) — like the rest
   of the content, no convergence. CD's player can show its tracklist instead of
   the bar, so there's no reliable target to rise to. */
.screensaver-overlay.screensaver-leave-active .progress-section {
  animation: screensaverLeaveUp 300ms ease-out forwards;
}

/* Convergence (Spotify always, CD when showing the player not its tracklist):
   rise all the way to AudioPlayerFull's bar position (above the controls) so the
   two bars read as one during the crossfade. Gated by the progressConverges prop. */
.screensaver-overlay.screensaver-leave-active .progress-section.progress-leave-converge {
  animation: screensaverLeaveProgressUp 300ms ease-out forwards;
}

.screensaver-overlay.screensaver-leave-active .simple-screensaver {
  animation: screensaverLeaveUp 300ms ease-out forwards;
}

/* Progress bar → up to AudioPlayerFull's bar position (above the controls). */
@keyframes screensaverLeaveProgressUp {
  from {
    opacity: 1;
    transform: translateY(0);
  }

  to {
    opacity: 0;
    transform: translateY(calc(-1 * var(--screensaver-leave-dy)));
  }
}

/* Title/subtitle → half that distance (centers in the space the controls free). */
@keyframes screensaverLeaveTitleUp {
  from {
    opacity: 1;
    transform: translateY(0);
  }

  to {
    opacity: 0;
    transform: translateY(calc(-0.5 * var(--screensaver-leave-dy)));
  }
}

/* Simple mode → the revealed status card is a centered layout
   with no matching anchor, so a plain gentle rise. */
@keyframes screensaverLeaveUp {
  from {
    opacity: 1;
    transform: translateY(0);
  }

  to {
    opacity: 0;
    transform: translateY(calc(-1 * var(--space-06)));
  }
}

@keyframes fadeIn {
  from {
    opacity: 0;
  }

  to {
    opacity: 1;
  }
}

@keyframes fadeOut {
  from {
    opacity: 1;
  }

  to {
    opacity: 0;
  }
}

/* Full-screen blurred background */
.artwork-background {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  width: 100%;
  height: 100%;
  z-index: 0;
  pointer-events: none;
  display: flex;
  align-items: center;
  justify-content: center;
}

.artwork-background .background-image {
  max-width: none;
  max-height: none;
  width: auto;
  height: auto;
  min-width: 150%;
  min-height: 150%;
  object-fit: contain;
  transform: scale(1.5) translateZ(0);
  filter: blur(var(--blur-05)) saturate(1.5);
  opacity: 0.16;
  -webkit-backface-visibility: hidden;
  backface-visibility: hidden;
}

/* Dark overlay to replicate contrast(1.5) brightness(0.5) effect without extra GPU filter passes */
.artwork-background::after {
  content: '';
  position: absolute;
  inset: 0;
  background: rgba(0, 0, 0, 0.55);
  z-index: 1;
  pointer-events: none;
}

/* === LAYOUT === */

.now-playing-screensaver {
  display: flex;
  width: 100%;
  height: 100%;
  padding: var(--space-05) var(--space-06) var(--space-05) var(--space-05);
  gap: var(--space-06);
  position: relative;
  z-index: 1;
}

/* Artwork */
.artwork-section {
  flex-shrink: 0;
  aspect-ratio: 1;
  z-index: 2;
}

.artwork-container {
  position: relative;
  width: 100%;
  height: 100%;
}

.artwork-blur {
  position: absolute;
  top: 50%;
  left: 50%;
  width: 116vw;
  height: 116vw;
  transform: translate(-50%, -50%) translateZ(0);
  z-index: 1;
  background-size: cover;
  background-position: center;
  filter: blur(var(--blur-05)) saturate(1.5);
  opacity: .12;
  will-change: transform;
  -webkit-backface-visibility: hidden;
  backface-visibility: hidden;
  contain: strict;
}

.artwork {
  position: relative;
  z-index: 3;
  width: 100%;
  height: 100%;
  border-radius: var(--radius-07);
  overflow: hidden;
  box-shadow: var(--shadow-artwork);
  pointer-events: none;
}

.artwork img,
.artwork .artwork-fallback {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

/* Inline-SVG station avatar fills its wrapper like the real artwork. */
.artwork-fallback {
  display: block;
}

/* The two non-avatar fallbacks. Both are drawn for contrast against a card
   rather than against this overlay's black, so they get the same ground
   AudioPlayerFull gives them — which is also what makes the leave crossfade
   land on an identical square instead of on a lighter or darker one. */
.artwork-placeholder,
.artwork-glyph {
  background: var(--color-background-strong);
}

.artwork-glyph {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--color-text-light);
}

/* Held cover while the next one decodes — the same veil AudioPlayerFull draws,
   because this view crossfades into it. The scale is not decoration: a blur
   samples past the element's edge, and without it the rounded corners show a
   translucent halo. */
.artwork > img,
.artwork > .artwork-fallback,
.artwork > .artwork-glyph {
  transition:
    filter var(--transition-medium),
    transform var(--transition-medium);
}

.artwork-pending > img,
.artwork-pending > .artwork-fallback,
.artwork-pending > .artwork-glyph {
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
  /* The spinner paints with currentColor and sits on a darkened cover. */
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

/* Content Section */
.content-section {
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  z-index: 1;
  min-width: 0;
}

.track-info {
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: center;
  text-align: center;
  gap: var(--space-03);
}

.track-title {
  color: var(--color-text-contrast);
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
}

.track-subtitle {
  color: var(--color-text-contrast);
  opacity: 0.8;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 1;
  -webkit-box-orient: vertical;
}

.track-subtitle.text-mono-medium {
  color: var(--color-text-contrast-50);
  opacity: 1;
}

/* Station bar (radio + Shazam only) */
.station-bar {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-03);
  padding-bottom: var(--space-06);
}

.station-favicon {
  width: 40px;
  height: 40px;
  border-radius: var(--radius-02);
  object-fit: cover;
  flex-shrink: 0;
}

.station-icon {
  flex-shrink: 0;
}

.station-name {
  color: var(--color-text-contrast);
  opacity: 0.8;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.progress-section {
  padding-bottom: var(--space-06);
}

/* === SIMPLE MODE (bluetooth, mac) === */

.simple-screensaver {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  position: relative;
  z-index: 1;
  text-align: center;
  width: 100%;
  height: 100%;
}

.simple-status {
  color: var(--color-text-contrast-50);
  margin-top: var(--space-05-fixed);
}

.simple-icon-invert :deep(.app-icon-svg) {
  filter: invert(1);
}

.simple-device-name {
  color: var(--color-text-contrast);
  white-space: pre-line;
  margin-top: var(--space-01);
}

/* === STAGGER ANIMATIONS === */

.stagger-1,
.stagger-2,
.stagger-3,
.stagger-4 {
  opacity: 0;
  transform: translateY(var(--space-05));
}

.screensaver-overlay .stagger-1,
.screensaver-overlay .stagger-2,
.screensaver-overlay .stagger-3,
.screensaver-overlay .stagger-4 {
  animation:
    stagger-transform var(--transition-spring) forwards,
    stagger-opacity 0.4s ease forwards;
}

.screensaver-overlay .stagger-1 { animation-delay: 400ms; }
.screensaver-overlay .stagger-2 { animation-delay: 400ms; }
.screensaver-overlay .stagger-3 { animation-delay: 500ms; }
.screensaver-overlay .stagger-4 { animation-delay: 600ms; }

@keyframes stagger-transform {
  to {
    transform: translateY(0);
  }
}

@keyframes stagger-opacity {
  to {
    opacity: 1;
  }
}
</style>
