<!-- LyricsPlaybackBar.vue — full-width playback bar pinned to the bottom of
     LyricsView. Its control surface scales with what the active source can
     actually do on the wire:
       - "full"     (spotify, cd, music_library): name/artist + interactive
                     progress bar + play/pause/prev/next, same generic
                     pause/resume/next/prev commands AudioPlayerFull uses.
       - "metadata" (airplay, dlna, qobuz): name/artist + a read-only progress
                     bar (these are receiver-controlled — no transport surface
                     on the wire; ConnectProgressBar already self-hides when a
                     source reports no duration, e.g. Qobuz).
       - "name-only" (radio): name/artist only — no seek, no track transport.
     Always visible by default; swipes down to hide, swipes up from the bottom
     edge to show again (useSwipeVisibility — the same gesture the Dock would
     normally own, freed up because useDockDrag ignores .lyrics-view). The
     arrow hint doubles as a tap toggle and as a swipe handle in both
     directions. -->

<template>
  <div ref="dragZone" class="lyrics-bar-drag-zone"></div>

  <!-- Persistent swipe affordance — stays mounted whether the bar itself is
       shown or hidden. Resting position tracks the bar's own (measured)
       height: just inside its top edge when shown, down near the bottom edge
       when the bar is hidden. Points down while the bar is shown and flips to
       point up once it's hidden (see .lyrics-bar-swipe-hint). Also a tap
       target for the same show/hide the swipe performs, so it's a real button
       (aria-expanded, not aria-hidden) rather than decoration. -->
  <button ref="hint" type="button" class="lyrics-bar-swipe-hint" :class="{ 'is-bar-visible': isVisible }"
    :style="{ '--bar-height': `${barHeight}px` }" :aria-label="t('lyrics.playbackControls')"
    :aria-expanded="isVisible" @pointerdown="onHintPointerDown" @click="onHintClick">
    <SvgIcon name="arrowExtended" :size="24" />
  </button>

  <!-- The bar AND its content stay mounted (the height must always be
       measurable for the swipe hint above, and unmounting the content would
       pop it out instantly instead of letting it travel with the bar) — show
       and hide are a plain class toggle, styled below. `inert` has to collapse
       to undefined rather than false: Vue renders inert="false" verbatim, and
       any value at all makes the subtree inert. -->
  <div ref="panel" class="lyrics-bar" :class="{ 'is-hidden': !isVisible }" :inert="isVisible ? undefined : true">
    <div class="lyrics-bar-content">
      <div class="lyrics-bar-track">
        <h2 class="heading-4 lyrics-bar-title">{{ identity.title }}</h2>
        <p class="text-body lyrics-bar-artist">{{ identity.artist }}</p>
      </div>

      <div v-if="tier !== 'name-only'" class="lyrics-bar-progress">
        <ConnectProgressBar :currentPosition="currentPosition" :duration="duration"
          :progressPercentage="progressPercentage" :isReady="isPositionInitialized"
          :interactive="tier === 'full'" @seek="seekTo" />
      </div>

      <!-- Right column: the transport when the source has one — same as
           AudioPlayer's desktop sidebar (music library's .ml-transport-main):
           ghost IconButtons, no pill behind them. On the "metadata" tier it
           stays as an empty column of the same width, so the progress bar
           keeps the exact same centred 44% share whether the transport is
           there or not. -->
      <div v-if="tier !== 'name-only'" class="lyrics-bar-controls" :class="{ 'is-spacer': tier !== 'full' }">
        <div v-if="tier === 'full'" class="playback-controls">
          <IconButton icon="previous" variant="ghost" size="small" @click="previousTrack" />
          <IconButton :icon="isPlaying ? 'pause' : 'play'" variant="ghost" size="medium"
            :loading="isBuffering" @click="togglePlayPause" />
          <IconButton icon="next" variant="ghost" size="small" :disabled="!hasNext" @click="nextTrack" />
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, onMounted, onUnmounted } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useCdStore } from '@/stores/cdStore';
import { getTrackIdentity } from '@/stores/lyricsStore';
import { useI18n } from '@/services/i18n';
import { useSourceProgress } from '@/composables/useSourceProgress';
import { useSwipeVisibility } from '@/composables/useSwipeVisibility';

import ConnectProgressBar from '@/components/audio/ConnectProgressBar.vue';
import IconButton from '@/components/ui/IconButton.vue';
import SvgIcon from '@/components/ui/SvgIcon.vue';

const props = defineProps({
  source: { type: String, required: true }
});

const { t } = useI18n();
const unifiedStore = useUnifiedAudioStore();
const cdStore = useCdStore();

const FULL_CONTROL_SOURCES = new Set(['spotify', 'cd', 'music_library']);
const NAME_ONLY_SOURCES = new Set(['radio']);

const tier = computed(() => {
  if (FULL_CONTROL_SOURCES.has(props.source)) return 'full';
  if (NAME_ONLY_SOURCES.has(props.source)) return 'name-only';
  return 'metadata';
});

const identity = computed(() => getTrackIdentity(props.source, unifiedStore.systemState.metadata));

const { currentPosition, duration, progressPercentage, seekTo, isPositionInitialized } =
  useSourceProgress(props.source, { compensateStaleness: true });

// Same wire commands as AudioPlayerFull — the "full" tier is exactly the set
// of sources whose backend COMMANDS include generic pause/resume/next/prev.
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

const isPlaying = computed(() => unifiedStore.systemState.metadata?.is_playing || false);

// Mirrors AudioPlayerFull's isBuffering: spinner until audio actually flows,
// plus CD's disc-identity warm-up window.
const isBuffering = computed(() => {
  const meta = unifiedStore.systemState.metadata || {};
  if (meta.is_buffering) return true;
  if (props.source === 'cd' && meta.disc_present && (!meta.cache_ready || !meta.disc_id)) {
    return true;
  }
  return false;
});

// Only CD has a "last track" concept; other full-control sources have no
// bound on next.
const hasNext = computed(() => {
  if (props.source !== 'cd') return true;
  return !cdStore.currentTrack || cdStore.currentTrack < cdStore.tracks.length;
});

// === Swipe show/hide ===
const dragZone = ref(null);
const panel = ref(null);
const hint = ref(null);
// Starts hidden for exactly one painted frame so opening Lyrics plays the very
// same slide-in the swipe does — one code path for both, instead of a separate
// mount-only keyframe. Two rAFs: the first still runs inside the frame that
// mounted us, so the browser needs the second to have painted the hidden state
// and have something to transition from.
const isVisible = ref(false);
onMounted(() => {
  requestAnimationFrame(() => requestAnimationFrame(() => { isVisible.value = true; }));
});

useSwipeVisibility({
  dragZone,
  panel,
  handle: hint,
  isVisible,
  onShow: () => { isVisible.value = true; },
  onHide: () => { isVisible.value = false; },
});

// Tapping the hint toggles the bar. Its bar-hidden resting spot sits inside
// the swipe band, so an upward swipe started on the arrow drives
// useSwipeVisibility AND ends in a native click — that click has to be
// dropped or the bar would flip straight back. Same travel budget the v-press
// directive uses to tell a tap from a drag.
const TAP_SLOP_PX = 10;
let pressY = null;

function onHintPointerDown(event) {
  pressY = event.clientY;
}

function onHintClick(event) {
  const swiped = pressY !== null && Math.abs(event.clientY - pressY) > TAP_SLOP_PX;
  pressY = null;
  if (!swiped) isVisible.value = !isVisible.value;
}

// The bar's own height (varies by tier — e.g. "full" is taller than
// "name-only") so the swipe hint can rest against its top edge when shown.
// The bar stays mounted (never v-if'd) specifically so this stays measurable
// even while hidden/translated off-screen.
const barHeight = ref(0);
let barResizeObserver = null;
onMounted(() => {
  if (!panel.value) return;
  barResizeObserver = new ResizeObserver(() => {
    barHeight.value = panel.value?.offsetHeight || 0;
  });
  barResizeObserver.observe(panel.value);
});
onUnmounted(() => barResizeObserver?.disconnect());
</script>

<style scoped>
.lyrics-bar-drag-zone {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  height: 12%;
  z-index: 2;
  pointer-events: none;
}

.lyrics-bar {
  background: linear-gradient(to bottom, transparent 0%, var(--color-background-scrim) 100%);
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 3;
  display: flex;
  align-items: center;
  gap: var(--space-06);
  padding: var(--space-05) var(--space-07);
  /* Headroom for the swipe hint's 40px tap target, which now rests inside this
     top edge: without it the hint lands on the title row in the short tiers
     ("name-only", "metadata"), which have no vertical slack. --space-05-fixed
     doesn't shrink on mobile, so the reserved band keeps matching the hint
     (24px glyph + 2 × --space-02) at every breakpoint. */
  padding-top: calc(var(--space-06) + var(--space-05-fixed));
  padding-bottom: max(var(--space-06), env(safe-area-inset-bottom, 0px));
  /* Stays mounted always (see script) — slides/fades via this class toggle. The
     panel itself is deliberately NOT sprung: an overshoot past translateY(0)
     lifts its bottom edge off the screen edge and opens a gap under the
     gradient. The spring lives on the content instead (below), which can
     overshoot freely inside the bar. The content stays mounted with the bar
     too: translateY(100%) is measured against the bar's own height, so
     unmounting the children would collapse that height mid-transition and the
     bar would barely move. */
  transition: transform var(--transition-normal), opacity var(--transition-normal);
}

.lyrics-bar.is-hidden {
  transform: translateY(100%);
  opacity: 0;
  pointer-events: none;
}

.lyrics-bar-content {
  display: contents;
}

/* The three sections rise + fade in behind the bar's own travel (same staggered
   entrance as AudioPlayerFull's track-info/controls-section), and fade back out
   with it. Transitions rather than keyframes so both directions replay on every
   toggle — an `animation ... forwards` only ever runs on mount and would then
   pin these properties, overriding anything the hide state sets. The stagger
   lives on the shown selector alone: a transition takes its timing from the
   state it moves *to*, so the way out has no delay and the bar leaves as one
   piece. */
.lyrics-bar-track,
.lyrics-bar-progress,
.lyrics-bar-controls {
  transition: transform var(--transition-spring), opacity var(--transition-normal);
}

.lyrics-bar.is-hidden .lyrics-bar-track,
.lyrics-bar.is-hidden .lyrics-bar-progress,
.lyrics-bar.is-hidden .lyrics-bar-controls {
  opacity: 0;
  transform: translateY(var(--space-04));
}

.lyrics-bar:not(.is-hidden) .lyrics-bar-progress { transition-delay: 60ms; }
.lyrics-bar:not(.is-hidden) .lyrics-bar-controls { transition-delay: 120ms; }

/* Basis accounts for the row's two gaps (gap isn't excluded from percentage
   flex-basis automatically) so 28/44/28 sums to the container's true content
   width instead of overflowing it. */
.lyrics-bar-track {
  flex: 0 0 calc((100% - 2 * var(--space-06)) * 0.28);
  min-width: 0;
}

.lyrics-bar-title,
.lyrics-bar-artist {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.lyrics-bar-title {
  color: var(--color-text-contrast);
}

.lyrics-bar-artist {
  color: var(--color-text-contrast-50);
}

.lyrics-bar-progress {
  /* Fills whatever's left after the two 28% columns — i.e. the 44% middle share,
     without a third magic number to keep in sync. */
  flex: 1 1 0;
  min-width: 0;
}

/* Persistent swipe affordance, independent of the bar's own mount state —
   centered, stacked over the bar (z-index) so it stays visible whether the
   bar is shown or hidden. Resting position tracks the bar: hidden → 24/32px
   off the bottom edge (--space-06, which is already 24px on mobile / 32px on
   desktop); shown → its bottom edge lands on the bar's own (measured) top
   edge, then translateY pushes it back down by exactly its own height, so it
   sits inside the bar's top band instead of floating over the lyrics. That
   100% is deliberately self-measuring: the tap target's padding can change
   without a matching magic number here. The wrapper owns position and hit
   area only; the arrow's direction is flipped on the injected <path> below. */
.lyrics-bar-swipe-hint {
  position: absolute;
  left: 50%;
  /* 24px between the glyph and the bottom edge, at every breakpoint: the tap
     padding is invisible, so it's subtracted here rather than shifting the
     arrow up by 8px. --space-05-fixed and --space-02 both hold their value on
     mobile (--space-06, used before, was 32px desktop / 24px mobile). */
  bottom: calc(var(--space-05-fixed) - var(--space-02));
  transform: translate(-50%, 0);
  z-index: 4;
  display: flex;
  /* 24px glyph + this padding = a 40px tap target; the bare glyph is too small
     to hit reliably on the kiosk touchscreen. */
  padding: var(--space-02);
  cursor: pointer;
  color: var(--color-text-contrast-50);
  transition: bottom var(--transition-spring-light), transform var(--transition-spring-light);
}

.lyrics-bar-swipe-hint.is-bar-visible {
  bottom: var(--bar-height, 0px);
  transform: translate(-50%, 100%);
}

/* Flip the chevron to face the direction the swipe will take: down while the
   bar is shown (swipe down to hide), up once it's hidden. Mirroring about
   y=9.6 — the line through arrow-extended.svg's two endpoints — pins those
   endpoints and moves only the middle point (14.63 → 4.57), so the arrow
   inverts in place instead of tumbling; the rounded apex mirrors along with
   it. non-scaling-stroke is what makes it work: transform scales stroke width
   too, so without it the trace thins to nothing as it passes through flat.
   Both states must name scaleY() explicitly — interpolating from `none` goes
   through a matrix decomposition that can surface a negative scale as a
   180deg rotation. */
.lyrics-bar-swipe-hint :deep(path) {
  vector-effect: non-scaling-stroke;
  transform-box: view-box;
  transform-origin: 12px 9.6px;
  transform: scaleY(-1);
  transition: transform var(--transition-normal);
}

.lyrics-bar-swipe-hint.is-bar-visible :deep(path) {
  transform: scaleY(1);
}

/* Same basis as .lyrics-bar-track (identical calc, gaps included) so the row
   reads 28/44/28 and the progress bar sits optically centred. The transport is
   fixed-size (~176px: 50 + 60 + 50 + two 8px gaps) and can't shrink, but 28%
   stays well above that down to ~630px of content width — narrower than that is
   already the stacked mobile layout below. */
.lyrics-bar-controls {
  flex: 0 0 calc((100% - 2 * var(--space-06)) * 0.28);
  display: flex;
  justify-content: center;
}

.playback-controls {
  display: flex;
  align-items: center;
  gap: var(--space-02);
}

/* Reskin the shared components' light-surface defaults for this always-dark
   bar — same -contrast token set the rest of LyricsView uses over its
   blurred artwork backdrop. */
.lyrics-bar-progress :deep(.time) {
  color: var(--color-text-contrast-50);
}
.lyrics-bar-progress :deep(.progress-container) {
  background-color: var(--color-background-neutral-12);
}
.lyrics-bar-progress :deep(.progress) {
  background-color: var(--color-text-contrast);
}

/* Transport sizing copied verbatim from AudioPlayer's desktop sidebar rules
   (.ml-transport-main): 34px prev/next, 44px play/pause + its spinner. Applied
   at every aspect ratio here — the lyrics bar has one control layout, so the
   icons keep the desktop scale on the kiosk and on a phone alike. */
.lyrics-bar-controls :deep(.icon-button--small .svg-responsive) {
  width: 34px;
  height: 34px;
}

.lyrics-bar-controls :deep(.icon-button--medium .svg-responsive) {
  width: 44px;
  height: 44px;
}

.lyrics-bar-controls :deep(.icon-button--medium.icon-button--loading .loading-spinner--medium) {
  --spinner-size: 44px;
}

.lyrics-bar-controls :deep(.icon-button--medium.icon-button--loading .loading-spinner--medium .loading-spinner-content) {
  transform: scale(0.85);
}

/* Portrait/mobile: the transport + track-info + progress bar don't fit in one
   row at phone widths — stack each section on its own row instead. */
@media (max-aspect-ratio: 4/3) {
  .lyrics-bar {
    flex-wrap: wrap;
    gap: var(--space-03) var(--space-04);
    padding-inline: var(--space-05);
  }

  .lyrics-bar-track {
    flex-basis: 100%;
  }

  .lyrics-bar-progress {
    flex-basis: 100%;
  }

  .lyrics-bar-controls {
    flex-basis: 100%;
    display: flex;
    justify-content: center;
  }

  /* Stacked rows are already full-width and centred, so the spacer column has
     no work left to do — keeping it would only add an empty row + row gap. */
  .lyrics-bar-controls.is-spacer {
    display: none;
  }
}
</style>
