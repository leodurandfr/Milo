<!-- ProgressBar.vue — the one playback progress bar: elapsed / track / total.
     Positions and durations are ALWAYS milliseconds, the wire convention
     (`position_ms`), so no caller converts on the way in or out; `seek` is
     emitted in ms too.
       - variant "light" (default): dark fill on a light surface — the standard
         player card.
       - variant "dark": light fill, for the always-dark surfaces that render
         over artwork (lyrics bar, screensaver, mini-player cards).
     Self-hides when the source reports no duration (e.g. Qobuz, radio). -->

<template>
  <div class="progress-bar" :class="[`progress-bar--${variant}`, { 'progress-bar--animated': animateIn }]"
    v-if="duration > 0 && isReady">
    <span class="text-mono time">{{ formatTime(currentPosition) }}</span>
    <div class="progress-container" :class="{ interactive }" @click="onProgressClick">
      <div class="progress" :style="progressStyle"></div>
    </div>
    <span class="text-mono time">{{ formatTime(duration) }}</span>
  </div>
</template>

<script setup>
import { computed } from 'vue';

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
  variant: {
    type: String,
    default: 'light',
    validator: (v) => ['light', 'dark'].includes(v)
  },
  // Spring rise + fade on mount, for the surfaces whose whole player stages in
  // (AudioPlayerFull, screensaver, lyrics bar). Off for bars that are already
  // part of a staged parent.
  animateIn: {
    type: Boolean,
    default: false
  }
});

const emit = defineEmits(['seek']);

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
   fade, matching the tracklist-content / player stagger. */
.progress-bar--animated {
  opacity: 0;
  transform: translateY(var(--space-05));
  animation:
    stagger-transform var(--transition-spring) forwards,
    stagger-opacity 0.4s ease forwards;
}

@keyframes stagger-transform {
  to {
    transform: none;
  }
}

@keyframes stagger-opacity {
  to {
    opacity: 1;
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

.progress-bar--light .progress-container {
  background-color: var(--color-background-strong);
}

.progress-bar--light .progress {
  background-color: var(--color-background-contrast);
}

.progress-bar--light .time {
  color: var(--color-text-light);
}

.progress-bar--dark .progress-container {
  background-color: var(--color-background-neutral-12);
}

.progress-bar--dark .progress {
  background-color: var(--color-background-neutral);
}

.progress-bar--dark .time {
  color: var(--color-text-contrast-50);
}
</style>
