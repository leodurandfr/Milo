<!-- frontend/src/components/cd/TrackCard.vue -->
<template>
  <div
    class="track-card"
    :class="{ 'track-card--current': isCurrent }"
    @click="$emit('play', track.number)"
  >
    <!-- Track number or playing indicator -->
    <div class="track-number text-mono">
      <div v-if="isCurrent && isPlaying" class="playing-indicator">
        <span class="bar"></span>
        <span class="bar"></span>
        <span class="bar"></span>
      </div>
      <span v-else>{{ track.number }}</span>
    </div>

    <!-- Track title -->
    <div class="track-title text-body">{{ track.title || t('audioSources.cdSource.trackN', { n: track.number }) }}</div>

    <!-- Track duration -->
    <div class="track-duration text-mono-small">{{ formatDuration(track.duration) }}</div>
  </div>
</template>

<script setup>
import { useI18n } from '@/services/i18n';

const { t } = useI18n();

defineProps({
  track: {
    type: Object,
    required: true
  },
  isCurrent: {
    type: Boolean,
    default: false
  },
  isPlaying: {
    type: Boolean,
    default: false
  }
});

defineEmits(['play']);

function formatDuration(seconds) {
  if (!seconds) return '0:00';
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, '0')}`;
}
</script>

<style scoped>
.track-card {
  display: flex;
  align-items: center;
  gap: var(--space-03);
  padding: var(--space-04) 0;
  border-bottom: 1px solid var(--color-border);
  cursor: pointer;
  transition: var(--transition-press);
}

.track-card:last-child {
  border-bottom: none;
}

@media (max-aspect-ratio: 4/3) {
  .track-card:last-child {
    padding-bottom: var(--space-08);
  }
}

.track-number {
  width: 28px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--color-text-secondary);
}

.track-title {
  flex: 1;
  color: var(--color-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.track-duration {
  flex-shrink: 0;
  color: var(--color-text-secondary);
}

/* Current track: all text in brand color */
.track-card--current .track-number,
.track-card--current .track-title,
.track-card--current .track-duration {
  color: var(--color-brand);
}

/* Playing indicator animation */
.playing-indicator {
  display: flex;
  align-items: flex-end;
  justify-content: center;
  gap: 2px;
  height: 14px;
}

.playing-indicator .bar {
  display: block;
  width: 3px;
  background: var(--color-brand);
  border-radius: 1px;
  animation: bar-bounce 0.8s ease-in-out infinite;
}

.playing-indicator .bar:nth-child(1) {
  height: 60%;
  animation-delay: 0s;
}

.playing-indicator .bar:nth-child(2) {
  height: 100%;
  animation-delay: 0.15s;
}

.playing-indicator .bar:nth-child(3) {
  height: 40%;
  animation-delay: 0.3s;
}

@keyframes bar-bounce {
  0%, 100% { transform: scaleY(0.4); }
  50% { transform: scaleY(1); }
}
</style>
