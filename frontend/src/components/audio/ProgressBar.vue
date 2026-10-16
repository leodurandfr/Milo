<!-- ProgressBar.vue — the one playback progress bar: elapsed / track / total.
     Positions and durations are ALWAYS milliseconds, the wire convention
     (`position_ms`), so no caller converts on the way in or out; `seek` is
     emitted in ms too.
     A seekable bar takes a press or a drag: the fill and the elapsed time
     follow the pointer, the track thickens while it is held, and one `seek`
     goes out on release — never during the drag, which would stutter mpv and
     flood a Connect daemon. A cancelled pointer seeks nothing. The arrow keys
     emit `skip` (seconds), the relative move, which a source adds to its own
     playhead.
       - variant "default": the strong fill on a track, on any panel — the full
         player. The fill drops to the muted one when the bar is not
         interactive (a source that cannot seek) — not for a short load, see
         `loading`.
       - variant "on-contrast": the light fill, for the surfaces that are dark
         in both themes and render over artwork (lyrics bar, the playing bar's
         card).
     Self-hides when the source reports no duration (e.g. Qobuz).
       - `live`: a live stream (radio), which has no playhead to show: the
         bare track, and the word "live" over its middle, the track fading
         out on either side of it. Dimmed while the stream does not play.
         Never seeks. -->

<template>
  <div v-if="live" class="progress-bar progress-bar--live"
    :class="[`progress-bar--${variant}`, { 'progress-bar--animated': animateIn, 'is-on-air': onAir }]">
    <span class="live-line" aria-hidden="true"></span>
    <span class="text-mono-medium time live-label">{{ t('player.live') }}</span>
    <span class="live-line live-line--end" aria-hidden="true"></span>
  </div>
  <div class="progress-bar" v-else-if="duration > 0 && isReady"
    :class="[`progress-bar--${variant}`, { 'progress-bar--animated': animateIn, 'is-scrubbing': scrubbing }]">
    <span class="text-mono-medium time">{{ formatTime(shownPosition) }}</span>
    <div ref="container" class="progress-container"
      :class="{ interactive, dimmed: !looksInteractive }" v-bind="sliderAttrs"
      @pointerdown="onPointerDown" @pointermove="onPointerMove" @pointerup="onPointerUp"
      @pointercancel="onPointerLost" @lostpointercapture="onPointerLost" @touchstart="onTouchStart"
      @keydown="onKeydown">
      <div class="progress-track">
        <div class="progress" :style="progressStyle"></div>
      </div>
    </div>
    <span class="text-mono-medium time">{{ formatTime(duration) }}</span>
  </div>
</template>

<script setup>
import { computed, ref, watch, onBeforeUnmount } from 'vue';
import { useDelayedFlag } from '@/composables/useDelayedFlag';
import { useI18n } from '@/services/i18n';
import { seekPositionAt } from '@/utils/scrub';

const props = defineProps({
  // Both in milliseconds.
  currentPosition: {
    type: Number,
    default: 0
  },
  duration: {
    type: Number,
    default: 0
  },
  progressPercentage: {
    type: Number,
    default: 0
  },
  isReady: {
    type: Boolean,
    default: true
  },
  interactive: {
    type: Boolean,
    default: true
  },
  // The session is loading. Sources withdraw `seek` meanwhile, and every track
  // change passes through it: the bar keeps the look it had until the load
  // outlasts the wait indicator, so it does not dim on each skip. Clicks follow
  // `interactive` regardless — a seek sent now would be refused.
  loading: {
    type: Boolean,
    default: false
  },
  variant: {
    type: String,
    default: 'default',
    validator: (v) => ['default', 'on-contrast'].includes(v)
  },
  // A live stream: the "live" bar, dimmed unless `onAir`.
  live: {
    type: Boolean,
    default: false
  },
  // The live stream plays (not loading, not stopped).
  onAir: {
    type: Boolean,
    default: false
  },
  // Spring rise + fade on mount, for the surfaces whose whole player stages in
  // (AudioPlayerFull, lyrics bar). Off for bars that are already
  // part of a staged parent.
  animateIn: {
    type: Boolean,
    default: false
  }
});

const emit = defineEmits(['seek', 'skip', 'track-width']);
const { t } = useI18n();

const longLoad = useDelayedFlag(() => props.loading);
const looksInteractive = ref(props.interactive);
watch(
  () => [props.interactive, props.loading && !longLoad.value],
  ([interactive, held]) => {
    if (!held) looksInteractive.value = interactive;
  }
);

// Computed to guarantee a valid numeric value
const progressPercent = computed(() => {
  const val = parseFloat(props.progressPercentage);
  return isNaN(val) ? 0 : Math.min(100, Math.max(0, val));
});

// The arrow keys' relative move, in seconds.
const KEY_STEP_S = 5;

const container = ref(null);
// The position under the pointer while a press is held; null otherwise.
const dragMs = ref(null);
const scrubbing = computed(() => dragMs.value !== null);
let pointerId = null;
// The track's box, measured once per press: it does not move during a drag,
// and a measure per pointermove would force a layout on every event.
let trackRect = null;

// The track's width in device pixels, told to whoever ticks the playhead
// (useSourceProgress): a tick that moves the fill by less than one draws nothing
// new. Observed rather than measured per tick, so it costs a layout only when
// the bar changes size.
let widthObserver = null;
watch(container, (el) => {
  widthObserver?.disconnect();
  widthObserver = null;
  if (!el) return;
  widthObserver = new ResizeObserver(([entry]) => {
    emit('track-width', entry.contentRect.width * window.devicePixelRatio);
  });
  widthObserver.observe(el);
});
onBeforeUnmount(() => {
  widthObserver?.disconnect();
  emit('track-width', 0);
});

const shownPosition = computed(() => dragMs.value ?? props.currentPosition);
const shownPercent = computed(() => (
  scrubbing.value ? (dragMs.value / props.duration) * 100 : progressPercent.value
));

const sliderAttrs = computed(() => (props.interactive ? {
  role: 'slider',
  tabindex: 0,
  'aria-label': t('player.position'),
  'aria-valuemin': 0,
  'aria-valuemax': props.duration,
  'aria-valuenow': shownPosition.value,
  'aria-valuetext': formatTime(shownPosition.value)
} : {}));

// The full-width fill is translated rather than resized so the progress ticks
// cost no layout. The visible region is [0, p%] with the rounded right cap
// emerging at low percentages, and the percentage is relative to the fill's own
// width. No CSS transition on purpose: a tick moves the fill by about one device
// pixel (useSourceProgress ticks per pixel, from `track-width`), so a
// transition would only add frames that draw nothing new.
const progressStyle = computed(() => ({
  transform: `translateX(${shownPercent.value - 100}%)`
}));

function formatTime(ms) {
  if (!ms) return '0:00';
  const totalSeconds = Math.floor(ms / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) {
    return `${hours}:${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
  }
  return `${minutes}:${seconds.toString().padStart(2, '0')}`;
}

function positionAt(event) {
  return seekPositionAt(event.clientX, trackRect.left, trackRect.width, props.duration);
}

// No preventDefault here: it would also withhold the compatibility mouse
// events useScreenActivity counts as a presence. `user-select` keeps a mouse
// drag from selecting the times instead.
function onPointerDown(event) {
  if (!props.interactive || !event.isPrimary || event.button !== 0) return;
  container.value.setPointerCapture(event.pointerId);
  pointerId = event.pointerId;
  trackRect = container.value.getBoundingClientRect();
  dragMs.value = positionAt(event);
}

function onPointerMove(event) {
  if (event.pointerId === pointerId) dragMs.value = positionAt(event);
}

// The seek first: the caller holds its target at once, so the fill hands over
// to it without a frame back at the old position.
function onPointerUp(event) {
  if (event.pointerId !== pointerId) return;
  const position = positionAt(event);
  endScrub();
  emit('seek', position);
}

// Cancelled by the system, or the capture lost: nothing is sent. Also fires
// after a release, which has already ended the scrub.
function onPointerLost(event) {
  if (event.pointerId === pointerId) endScrub();
}

function endScrub() {
  const id = pointerId;
  pointerId = null;
  dragMs.value = null;
  if (id !== null && container.value?.hasPointerCapture(id)) container.value.releasePointerCapture(id);
}

// A withdrawn seek under the finger (a load, a track change) ends the drag
// unsent. A duration corrected mid-drag does not: the release reads the
// fraction against the duration it then has.
watch(() => props.interactive, endScrub);

// A drag here is the bar's alone: the touch is claimed rather than stopped, so
// the full player's pull down leaves it (usePullToDismiss reads the claim)
// while the presence listeners on the document still see it.
function onTouchStart(event) {
  if (props.interactive && event.cancelable) event.preventDefault();
}

// One press, one skip: an auto-repeated key would send a burst of them to the
// daemon. A key held with a modifier is the browser's or the system's.
function onKeydown(event) {
  if (!props.interactive || event.repeat) return;
  if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
  const direction = { ArrowLeft: -1, ArrowRight: 1 }[event.key];
  if (!direction) return;
  event.preventDefault();
  emit('skip', direction * KEY_STEP_S);
}
</script>

<style scoped>
.progress-bar {
  display: flex;
  align-items: center;
  width: 100%;
  gap: var(--space-03);
  user-select: none;
}

/* Entrance when playback starts and the bar is v-if-mounted: spring rise +
   fade, matching the tracklist-content / player stagger. From-only keyframes
   and `backwards`: once risen the bar is back on its own styles, so an
   opacity it is given later (the live bar's dim) takes effect. */
.progress-bar--animated {
  animation:
    stagger-transform var(--transition-spring) backwards,
    stagger-opacity 0.4s ease backwards;
}

@keyframes stagger-transform {
  from {
    transform: translateY(var(--space-05));
  }
}

@keyframes stagger-opacity {
  from {
    opacity: 0;
  }
}

.progress-container {
  flex-grow: 1;
  display: flex;
  align-items: center;
  /* The hit zone, tall enough for a finger, laid over the row by its negative
     margins: in the layout it is the track's 8px, so neither the row nor what
     sits around it moves when the track thickens inside it. */
  height: 32px;
  margin-block: -12px;
  cursor: default;
}

.progress-container.interactive {
  cursor: pointer;
  touch-action: none;
}

.progress-container:focus-visible {
  outline: none;
}

/* Its height is animated rather than scaled: a scaleY would flatten the round
   caps into ellipses. It changes only when a press begins and ends, never on
   the playback ticks, which move the fill on the compositor. */
.progress-track {
  position: relative;
  width: 100%;
  height: 8px;
  border-radius: var(--radius-full);
  overflow: hidden;
  transition: height var(--transition-spring-light);
}

.is-scrubbing .progress-track {
  height: 12px;
  transition: height var(--transition-fast);
}

.progress-container:focus-visible .progress-track {
  box-shadow: 0 0 0 2px var(--color-brand);
}

/* A mouse finds the bar before pressing it; a touch screen has no hover to
   stay stuck in. */
@media (hover: hover) {
  .progress-container.interactive:hover .progress-track {
    height: 10px;
  }

  .is-scrubbing .progress-container.interactive:hover .progress-track {
    height: 12px;
  }
}

.progress {
  width: 100%;
  height: 100%;
  border-radius: var(--radius-full);
  position: absolute;
  left: 0;
  top: 0;
}

/* === Variants === */

.progress-bar--default .progress-track {
  background-color: var(--color-fill-faint);
}

.progress-bar--default .progress {
  background-color: var(--color-fill);
}

.progress-bar--default .progress-container.dimmed .progress {
  background-color: var(--color-text-secondary);
}

.progress-bar--default .time {
  color: var(--color-text-secondary);
}

.progress-bar--on-contrast .progress-track {
  background-color: var(--color-glint);
}

.progress-bar--on-contrast .progress {
  background-color: var(--color-text-on-contrast);
}

.progress-bar--on-contrast .time {
  color: var(--color-text-on-contrast-secondary);
}

/* === Live === */

/* The track in two halves around the word, each fading out toward it. */
.live-line {
  flex: 1;
  height: 8px;
  border-radius: var(--radius-01);
  -webkit-mask-image: linear-gradient(to right, black calc(100% - var(--space-07)), transparent);
  mask-image: linear-gradient(to right, black calc(100% - var(--space-07)), transparent);
}

.live-line--end {
  -webkit-mask-image: linear-gradient(to left, black calc(100% - var(--space-07)), transparent);
  mask-image: linear-gradient(to left, black calc(100% - var(--space-07)), transparent);
}

.progress-bar--default .live-line {
  background-color: var(--color-fill-faint);
}

.progress-bar--on-contrast .live-line {
  background-color: var(--color-glint);
}

.live-label {
  text-transform: uppercase;
  white-space: nowrap;
}

.progress-bar--live {
  transition: opacity var(--transition-medium);
}

.progress-bar--live:not(.is-on-air) {
  opacity: var(--opacity-disabled);
}
</style>
