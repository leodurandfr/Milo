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
     normally own, freed up because useDockDrag ignores .lyrics-view). -->

<template>
  <div ref="dragZone" class="lyrics-bar-drag-zone"></div>

  <!-- Persistent swipe affordance — stays mounted (and static, no self-animation)
       whether the bar itself is shown or hidden, so there's always something at
       the bottom edge hinting a swipe is possible. -->
  <SvgIcon name="swipeIndicator" :size="24" class="lyrics-bar-swipe-hint" aria-hidden="true" />

  <Transition name="lyrics-bar">
    <div v-if="isVisible" ref="panel" class="lyrics-bar">
      <div class="lyrics-bar-track">
        <h2 class="heading-4 lyrics-bar-title">{{ identity.title }}</h2>
        <p class="text-body lyrics-bar-artist">{{ identity.artist }}</p>
      </div>

      <div v-if="tier !== 'name-only'" class="lyrics-bar-progress" :class="{ 'is-compact': tier === 'metadata' }">
        <ConnectProgressBar :currentPosition="currentPosition" :duration="duration"
          :progressPercentage="progressPercentage" :isReady="isPositionInitialized"
          :interactive="tier === 'full'" @seek="seekTo" />
      </div>

      <div v-if="tier === 'full'" class="lyrics-bar-controls">
        <PlaybackControls :isPlaying="isPlaying" :isBuffering="isBuffering" :hasNext="hasNext"
          @play-pause="togglePlayPause" @previous="previousTrack" @next="nextTrack" />
      </div>
    </div>
  </Transition>
</template>

<script setup>
import { computed, ref } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useCdStore } from '@/stores/cdStore';
import { getTrackIdentity } from '@/stores/lyricsStore';
import { useSourceProgress } from '@/composables/useSourceProgress';
import { useSwipeVisibility } from '@/composables/useSwipeVisibility';

import ConnectProgressBar from '@/components/audio/ConnectProgressBar.vue';
import PlaybackControls from '@/components/audio/PlaybackControls.vue';
import SvgIcon from '@/components/ui/SvgIcon.vue';

const props = defineProps({
  source: { type: String, required: true }
});

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
const isVisible = ref(true);

useSwipeVisibility({
  dragZone,
  panel,
  isVisible,
  onShow: () => { isVisible.value = true; },
  onHide: () => { isVisible.value = false; },
});
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
  padding: var(--space-04) var(--space-06);
  padding-bottom: max(var(--space-04), env(safe-area-inset-bottom, 0px));
}

/* The bar itself just slides/fades — no spring here. Its children (below)
   spring in individually once mounted, same stagger technique as
   AudioPlayerFull's track-info/controls-section entrance. */
.lyrics-bar-enter-active {
  transition: transform var(--transition-normal), opacity var(--transition-normal);
}
.lyrics-bar-leave-active {
  transition: transform var(--transition-fast-leave), opacity var(--transition-fast-leave);
}
.lyrics-bar-enter-from,
.lyrics-bar-leave-to {
  transform: translateY(100%);
  opacity: 0;
}

@keyframes lyrics-bar-child-transform {
  to { transform: none; }
}
@keyframes lyrics-bar-child-opacity {
  to { opacity: 1; }
}

.lyrics-bar-track,
.lyrics-bar-progress,
.lyrics-bar-controls {
  opacity: 0;
  transform: translateY(var(--space-04));
  animation:
    lyrics-bar-child-transform var(--transition-spring) forwards,
    lyrics-bar-child-opacity 0.4s ease forwards;
}
.lyrics-bar-track { animation-delay: 0ms; }
.lyrics-bar-progress { animation-delay: 60ms; }
.lyrics-bar-controls { animation-delay: 120ms; }

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
  /* Fills whatever's left after track (fixed) and controls (natural width) —
     see the comment on .lyrics-bar-controls for why a flat 44% doesn't work. */
  flex: 1 1 0;
  min-width: 0;
}

.lyrics-bar-progress.is-compact {
  flex: 0 1 360px;
  margin-left: auto;
}

/* Persistent swipe affordance, independent of the bar's own mount state —
   pinned at the very bottom edge, centered, sitting above the bar (z-index)
   so it stays visible whether the bar is shown or hidden. Static; it does
   not animate on its own. */
.lyrics-bar-swipe-hint {
  position: absolute;
  left: 50%;
  bottom: 0;
  transform: translateX(-50%);
  z-index: 4;
  color: var(--color-text-contrast);
  pointer-events: none;
}

/* PlaybackControls' three buttons (80/90/80px + internal padding) need
   ~324px minimum at their fixed size — wider than a strict 28% share of the
   row at kiosk width (~251px). flex-shrink can't force content-driven
   min-width below that, so it would overflow the viewport; give it its
   natural width instead and let the progress column (which can genuinely
   shrink) absorb the difference. */
.lyrics-bar-controls {
  flex: 0 0 auto;
  display: flex;
  justify-content: center;
}

/* Reskin the shared components' light-surface defaults for this always-dark
   bar — same -contrast token set the rest of LyricsView uses over its
   blurred artwork backdrop. */
.lyrics-bar-progress :deep(.time) {
  color: var(--color-text-contrast-50);
}
.lyrics-bar-progress :deep(.progress-container) {
  background-color: var(--color-background-contrast-32);
}
.lyrics-bar-progress :deep(.progress) {
  background-color: var(--color-text-contrast);
}

.lyrics-bar-controls :deep(.controls) {
  background: none;
}
.lyrics-bar-controls :deep(.icon-primary) {
  color: var(--color-text-contrast);
}
.lyrics-bar-controls :deep(.icon-secondary) {
  color: var(--color-text-contrast-50);
}

/* Portrait/mobile: PlaybackControls' buttons are fixed-size (80-90px) and
   don't fit alongside track-info + a progress bar in one row at phone
   widths — stack each section on its own row instead. */
@media (max-aspect-ratio: 4/3) {
  .lyrics-bar {
    flex-wrap: wrap;
    gap: var(--space-03) var(--space-04);
    padding-inline: var(--space-05);
  }

  .lyrics-bar-track {
    flex-basis: 100%;
  }

  .lyrics-bar-progress,
  .lyrics-bar-progress.is-compact {
    flex-basis: 100%;
    margin-left: 0;
  }

  .lyrics-bar-controls {
    flex-basis: 100%;
    display: flex;
    justify-content: center;
  }
}
</style>
