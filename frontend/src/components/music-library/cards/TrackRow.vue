<template>
  <div v-press class="track-row" :class="{ current }" @click="$emit('play')">
    <div class="track-index">
      <!-- Animated bars while this row is the one playing; otherwise the number. -->
      <div v-if="current && playing" class="playing-bars" aria-hidden="true">
        <span></span><span></span><span></span>
      </div>
      <span v-else class="track-number text-mono">{{ number }}</span>
    </div>

    <div class="track-main">
      <p class="track-title heading-4">{{ song.title || song.name }}</p>
      <p v-if="showArtist && song.artist" class="track-artist text-mono">{{ song.artist }}</p>
    </div>

    <span class="track-duration text-mono">{{ formatDuration(song.duration) }}</span>
  </div>
</template>

<script setup>
import { formatDuration } from '../format.js';

defineProps({
  song: {
    type: Object,
    required: true,
  },
  // Display position (1-based) shown in the index column.
  number: {
    type: [Number, String],
    required: true,
  },
  // This row is the active queue entry.
  current: {
    type: Boolean,
    default: false,
  },
  // Playback is actively running (vs. paused) for the current row.
  playing: {
    type: Boolean,
    default: false,
  },
  // Show the per-track artist line (mixed contexts: genre/playlist/search/queue).
  showArtist: {
    type: Boolean,
    default: false,
  },
});

defineEmits(['play']);
</script>

<style scoped>
.track-row {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: var(--space-03);
  padding: var(--space-02) var(--space-03);
  border-radius: var(--radius-03);
  cursor: pointer;
  min-width: 0;
  transition: background var(--transition-fast);
}

.track-row.current {
  background: var(--color-background-neutral-50);
}

.track-index {
  flex-shrink: 0;
  width: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.track-number {
  color: var(--color-text-secondary);
}

.track-row.current .track-number {
  color: var(--color-brand);
}

.track-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
  overflow: hidden;
}

.track-title {
  margin: 0;
  color: var(--color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.track-row.current .track-title {
  color: var(--color-brand);
}

.track-artist {
  margin: 0;
  color: var(--color-text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.track-duration {
  flex-shrink: 0;
  color: var(--color-text-secondary);
}

/* Now-playing equalizer bars */
.playing-bars {
  display: flex;
  align-items: flex-end;
  gap: 2px;
  height: 16px;
}

.playing-bars span {
  width: 3px;
  background: var(--color-brand);
  border-radius: var(--radius-01);
  animation: eq-bar 900ms ease-in-out infinite;
}

.playing-bars span:nth-child(1) { animation-delay: 0ms; }
.playing-bars span:nth-child(2) { animation-delay: 220ms; }
.playing-bars span:nth-child(3) { animation-delay: 440ms; }

@keyframes eq-bar {
  0%, 100% { height: 5px; }
  50% { height: 16px; }
}
</style>
