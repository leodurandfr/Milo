<template>
  <!-- "card" variant: Horizontal layout for episode lists -->
  <div :class="['episode-card', {
    active: isActive,
    playing: isPlaying,
    loading: isLoading
  }]" @click="$emit('click')">

    <div class="episode-image">
      <img v-if="episode.image_url" :src="episode.image_url" alt="" class="episode-img" @error="handleImageError" />
      <div v-else class="image-placeholder">
        <Icon name="podcast" :size="32" color="var(--color-text-light)" />
      </div>
    </div>

    <div class="episode-details">
      <p class="episode-title text-body">{{ episode.name }}</p>
      <p class="episode-subtitle text-mono">
        <template v-if="episode.podcast">{{ episode.podcast.name }} • </template>
        <template v-if="episode.duration">{{ formatDuration(episode.duration) }}</template>
        <template v-if="episode.date_published"> • {{ formatDate(episode.date_published) }}</template>
      </p>

      <!-- Progress bar if playback has started -->
      <div v-if="episode.playback_progress && episode.playback_progress.position > 0" class="progress-bar">
        <div class="progress-fill" :style="{ width: progressPercent + '%' }"></div>
      </div>
    </div>

    <!-- Loading spinner -->
    <div v-if="isLoading" class="loading-spinner-small"></div>

    <!-- Play/Pause icon -->
    <CircularIcon v-else-if="!isLoading" :icon="isPlaying ? 'pause' : 'play'" variant="light"
      @click.stop="$emit('play')" />
  </div>

  <!-- "now-playing" variant: Full player view -->
  <div v-if="variant === 'now-playing'" class="episode-now-playing">
    <!-- Background blur -->
    <div class="episode-art-background">
      <img v-if="episode.image_url" :src="episode.image_url" alt="" class="background-episode-img" />
    </div>

    <div class="episode-art">
      <img v-if="episode.image_url" :src="episode.image_url" alt="Episode artwork" class="current-episode-img"
        @error="handleImageError" />
      <div v-else class="artwork-placeholder">
        <Icon name="podcast" :size="64" color="var(--color-text-light)" />
      </div>
    </div>

    <div class="episode-info">
      <p class="episode-name display-1">{{ episode.name }}</p>
      <p class="podcast-name text-mono" v-if="episode.podcast">{{ episode.podcast.name }}</p>
    </div>

    <!-- Progress slider -->
    <div class="progress-section" v-if="duration > 0">
      <RangeSlider
        :modelValue="position"
        :min="0"
        :max="duration"
        @update:modelValue="$emit('seek', $event)"
      />
      <div class="time-display">
        <span class="text-mono">{{ formatTime(position) }}</span>
        <span class="text-mono">{{ formatTime(duration) }}</span>
      </div>
    </div>

    <div class="controls-wrapper">
      <CircularIcon :icon="episode.is_favorite ? 'heart' : 'heartOff'" variant="background-light"
        @click="$emit('favorite')" />

      <Button variant="background-light" :left-icon="isPlaying ? 'pause' : 'play'"
        @click="$emit('play')">
        {{ isPlaying ? 'Pause' : 'Play' }}
      </Button>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue';
import CircularIcon from '@/components/ui/CircularIcon.vue';
import Button from '@/components/ui/Button.vue';
import Icon from '@/components/ui/Icon.vue';
import RangeSlider from '@/components/ui/RangeSlider.vue';

const props = defineProps({
  episode: {
    type: Object,
    required: true
  },
  variant: {
    type: String,
    default: 'card', // 'card' or 'now-playing'
    validator: (value) => ['card', 'now-playing'].includes(value)
  },
  isActive: {
    type: Boolean,
    default: false
  },
  isPlaying: {
    type: Boolean,
    default: false
  },
  isLoading: {
    type: Boolean,
    default: false
  },
  position: {
    type: Number,
    default: 0
  },
  duration: {
    type: Number,
    default: 0
  }
});

defineEmits(['click', 'play', 'favorite', 'seek']);

const imageError = ref(false);

const handleImageError = () => {
  imageError.value = true;
};

const progressPercent = computed(() => {
  if (!props.episode.playback_progress || props.episode.playback_progress.duration === 0) {
    return 0;
  }
  return (props.episode.playback_progress.position / props.episode.playback_progress.duration) * 100;
});

function formatDuration(seconds) {
  if (!seconds) return '';
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);

  if (hours > 0) {
    return `${hours}h ${minutes}m`;
  }
  return `${minutes}m`;
}

function formatTime(seconds) {
  if (!seconds || isNaN(seconds)) return '0:00';

  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = Math.floor(seconds % 60);

  if (hours > 0) {
    return `${hours}:${minutes.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
  }
  return `${minutes}:${secs.toString().padStart(2, '0')}`;
}

function formatDate(dateString) {
  if (!dateString) return '';
  const date = new Date(dateString * 1000); // Taddy returns Unix timestamp
  const now = new Date();
  const diffDays = Math.floor((now - date) / (1000 * 60 * 60 * 24));

  if (diffDays === 0) return 'Today';
  if (diffDays === 1) return 'Yesterday';
  if (diffDays < 7) return `${diffDays}d ago`;
  if (diffDays < 30) return `${Math.floor(diffDays / 7)}w ago`;

  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}
</script>

<style scoped>
/* Card variant */
.episode-card {
  display: flex;
  align-items: center;
  gap: var(--space-04);
  padding: var(--space-04);
  background: var(--color-background-neutral);
  border-radius: var(--radius-05);
  cursor: pointer;
  transition: all var(--transition-fast);
  border: 1px solid var(--color-border);
}

.episode-card:hover {
  background: var(--color-background-strong);
  transform: translateY(-2px);
  box-shadow: var(--shadow-02);
}

.episode-card.active {
  background: var(--color-background-glass);
}

.episode-image {
  width: 64px;
  height: 64px;
  border-radius: var(--radius-03);
  overflow: hidden;
  flex-shrink: 0;
  background: var(--color-background-strong);
}

.episode-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.image-placeholder {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--color-background-glass);
}

.episode-details {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
}

.episode-title {
  font-weight: 500;
  color: var(--color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.episode-subtitle {
  color: var(--color-text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.progress-bar {
  height: 3px;
  background: var(--color-background-glass);
  border-radius: var(--radius-full);
  overflow: hidden;
  margin-top: var(--space-01);
}

.progress-fill {
  height: 100%;
  background: var(--color-brand);
  transition: width var(--transition-fast);
}

.loading-spinner-small {
  width: 24px;
  height: 24px;
  border: 2px solid var(--color-background-glass);
  border-top-color: var(--color-brand);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

/* Now-playing variant */
.episode-now-playing {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: var(--space-05);
  padding: var(--space-06);
  background: var(--color-background-neutral);
  border-radius: var(--radius-06);
  overflow: hidden;
}

.episode-art-background {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  opacity: 0.1;
  filter: blur(60px);
  transform: scale(1.5);
  z-index: 0;
}

.background-episode-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.episode-art {
  position: relative;
  width: 200px;
  height: 200px;
  margin: 0 auto;
  border-radius: var(--radius-05);
  overflow: hidden;
  background: var(--color-background-glass);
  z-index: 1;
}

.current-episode-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.artwork-placeholder {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
}

.episode-info {
  position: relative;
  text-align: center;
  z-index: 1;
}

.episode-name {
  font-weight: 600;
  color: var(--color-text);
  margin-bottom: var(--space-02);
}

.podcast-name {
  color: var(--color-text-secondary);
}

.progress-section {
  position: relative;
  z-index: 1;
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.time-display {
  display: flex;
  justify-content: space-between;
  color: var(--color-text-secondary);
}

.controls-wrapper {
  position: relative;
  display: flex;
  justify-content: center;
  gap: var(--space-04);
  z-index: 1;
}

/* Mobile adjustments */
@media (max-aspect-ratio: 4/3) {
  .episode-image {
    width: 56px;
    height: 56px;
  }

  .episode-art {
    width: 160px;
    height: 160px;
  }
}
</style>
