<template>
  <div class="episode-card" @click="$emit('select', episode)">
    <div class="card-image">
      <img
        :src="imageUrl"
        :alt="episode.name"
        loading="lazy"
        @error="imageError = true"
      />
      <Button
        class="play-button"
        variant="toggle"
        size="small"
        @click.stop="$emit('play', episode)"
      >
        <Icon :name="isCurrentlyPlaying ? 'pause' : 'play'" :size="20" />
      </Button>
    </div>

    <div class="card-content">
      <h4 class="episode-name">{{ episode.name }}</h4>
      <p v-if="podcastName" class="podcast-name">{{ podcastName }}</p>

      <div class="episode-meta">
        <span class="duration">{{ formattedDuration }}</span>
        <span class="separator">•</span>
        <span class="date">{{ formattedDate }}</span>
      </div>

      <!-- Progress bar if in progress -->
      <div v-if="hasProgress" class="progress-container">
        <div class="progress-bar">
          <div class="progress-fill" :style="{ width: progressPercent + '%' }"></div>
        </div>
        <span class="time-remaining">{{ timeRemaining }}</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { usePodcastStore } from '@/stores/podcastStore'
import Button from '@/components/ui/Button.vue'
import Icon from '@/components/ui/Icon.vue'

const props = defineProps({
  episode: {
    type: Object,
    required: true
  }
})

defineEmits(['select', 'play'])

const podcastStore = usePodcastStore()
const imageError = ref(false)

const imageUrl = computed(() => {
  if (imageError.value) return '/default-episode.png'
  return props.episode.image_url ||
    props.episode.podcast?.image_url ||
    '/default-episode.png'
})

const podcastName = computed(() => {
  return props.episode.podcast?.name || ''
})

const isCurrentlyPlaying = computed(() => {
  return podcastStore.currentEpisode?.uuid === props.episode.uuid && podcastStore.isPlaying
})

const hasProgress = computed(() => {
  const progress = props.episode.playback_progress
  return progress && progress.position > 0 && !progress.completed
})

const progressPercent = computed(() => {
  const progress = props.episode.playback_progress
  if (!progress || !progress.duration) return 0
  return (progress.position / progress.duration) * 100
})

const timeRemaining = computed(() => {
  const progress = props.episode.playback_progress
  if (!progress) return ''
  const remaining = progress.duration - progress.position
  return formatDuration(remaining) + ' restantes'
})

const formattedDuration = computed(() => {
  return formatDuration(props.episode.duration || 0)
})

const formattedDate = computed(() => {
  if (!props.episode.date_published) return ''
  return formatRelativeDate(props.episode.date_published)
})

function formatDuration(seconds) {
  if (!seconds || seconds <= 0) return '0 min'
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  if (h > 0) return `${h}h ${m}min`
  return `${m} min`
}

function formatRelativeDate(epochSeconds) {
  const date = new Date(epochSeconds * 1000)
  const now = new Date()
  const diff = now - date
  const days = Math.floor(diff / 86400000)

  if (days === 0) return "Aujourd'hui"
  if (days === 1) return 'Hier'
  if (days < 7) return `Il y a ${days} jours`
  if (days < 30) return `Il y a ${Math.floor(days / 7)} semaines`
  if (days < 365) return `Il y a ${Math.floor(days / 30)} mois`
  return `Il y a ${Math.floor(days / 365)} ans`
}
</script>

<style scoped>
.episode-card {
  display: flex;
  gap: var(--space-03);
  background: var(--color-background-subtle);
  border-radius: var(--radius-04);
  padding: var(--space-03);
  cursor: pointer;
  transition: background 0.2s ease;
}

.episode-card:hover {
  background: var(--color-background-neutral);
}

.card-image {
  position: relative;
  width: 80px;
  height: 80px;
  flex-shrink: 0;
  border-radius: var(--radius-03);
  overflow: hidden;
}

.card-image img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.play-button {
  position: absolute;
  bottom: var(--space-01);
  right: var(--space-01);
  width: 32px;
  height: 32px;
  border-radius: 50%;
}

.card-content {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
}

.episode-name {
  font-size: var(--font-size-sm);
  font-weight: var(--font-weight-semibold);
  color: var(--color-text);
  margin: 0;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.podcast-name {
  font-size: var(--font-size-xs);
  color: var(--color-text-muted);
  margin: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.episode-meta {
  font-size: var(--font-size-xs);
  color: var(--color-text-muted);
  display: flex;
  gap: var(--space-02);
}

.separator {
  opacity: 0.5;
}

.progress-container {
  margin-top: var(--space-02);
}

.progress-bar {
  height: 4px;
  background: var(--color-background-neutral);
  border-radius: 2px;
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  background: var(--color-accent);
  transition: width 0.3s ease;
}

.time-remaining {
  font-size: var(--font-size-xs);
  color: var(--color-accent);
  margin-top: var(--space-01);
  display: block;
}
</style>
