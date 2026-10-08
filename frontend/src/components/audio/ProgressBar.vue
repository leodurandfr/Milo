<!-- ProgressBar.vue — the one playback progress bar: elapsed / track / total.
     Positions and durations are ALWAYS milliseconds, the wire convention
     (`position_ms`), so no caller converts on the way in or out; `seek` is
     emitted in ms too.
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
  <div class="progress-bar" :class="[`progress-bar--${variant}`, { 'progress-bar--animated': animateIn }]"
    v-else-if="duration > 0 && isReady">
    <span class="text-mono-medium time">{{ formatTime(currentPosition) }}</span>
    <div class="progress-container" :class="{ interactive, dimmed: !looksInteractive }" @click="onProgressClick">
      <div class="progress" :style="progressStyle"></div>
    </div>
    <span class="text-mono-medium time">{{ formatTime(duration) }}</span>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue';
import { useDelayedFlag } from '@/composables/useDelayedFlag';
import { useI18n } from '@/services/i18n';

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

const emit = defineEmits(['seek']);
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

// The full-width fill is translated rather than resized so the 10 Hz progress
// ticks animate on the compositor (no layout). The visible region is [0, p%]
// with the rounded right cap emerging at low percentages, and the percentage
// is relative to the fill's own width — no pixel measurement needed. No CSS
// transition on purpose: at 10 Hz each tick moves the fill by a fraction of a
// pixel, so a transition only makes the bar lag behind the real position.
const progressStyle = computed(() => ({
  transform: `translateX(${progressPercent.value - 100}%)`
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

function onProgressClick(event) {
  if (!props.interactive || !props.duration) return;

  const container = event.currentTarget;
  const rect = container.getBoundingClientRect();
  const offsetX = event.clientX - rect.left;
  const percentage = offsetX / rect.width;

  const newPosition = Math.floor(props.duration * percentage);
  emit('seek', newPosition);
}
</script>

<style scoped>
.progress-bar {
  display: flex;
  align-items: center;
  width: 100%;
  gap: var(--space-03);
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
  height: 8px;
  border-radius: var(--radius-01);
  cursor: default;
  position: relative;
  overflow: hidden;
}

.progress-container.interactive {
  cursor: pointer;
}

.progress {
  width: 100%;
  height: 100%;
  border-radius: var(--radius-01);
  position: absolute;
  left: 0;
  top: 0;
}

/* === Variants === */

.progress-bar--default .progress-container {
  background-color: var(--color-track);
}

.progress-bar--default .progress {
  background-color: var(--color-fill);
}

.progress-bar--default .progress-container.dimmed .progress {
  background-color: var(--color-fill-muted);
}

.progress-bar--default .time {
  color: var(--color-text-tertiary);
}

.progress-bar--on-contrast .progress-container {
  background-color: var(--color-glint);
}

.progress-bar--on-contrast .progress {
  background-color: var(--color-fill-on-contrast);
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
  background-color: var(--color-track);
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
