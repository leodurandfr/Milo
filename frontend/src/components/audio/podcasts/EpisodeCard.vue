<template>
  <div class="episode-card" @click="$emit('select', episode)">
    <div class="card-image">
      <img :src="imageUrl" :alt="episode.name" loading="lazy" @error="imageError = true" />
    </div>

    <div class="card-content">
      <div class="content-info">
        <h4 class="episode-name text-body">{{ episode.name }}</h4>
        <p v-if="podcastName" class="podcast-name text-mono">{{ podcastName }}</p>

        <div class="episode-meta text-mono">
          <span class="duration">{{ hasProgress ? timeRemaining : formattedDuration }}</span>
          <span class="separator">•</span>
          <span class="date">{{ formattedDate }}</span>
        </div>
      </div>

      <IconButton :icon="isCurrentlyPlaying ? 'pause' : 'play'" type="light" :size="40"
        :loading="isCurrentEpisodeBuffering" :disabled="isCurrentEpisodeBuffering" @click.stop="handlePlayClick" />
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { usePodcastStore } from '@/stores/podcastStore'
import IconButton from '@/components/ui/IconButton.vue'

const props = defineProps({
  episode: {
    type: Object,
    required: true
  }
})

const $emit = defineEmits(['select', 'play', 'pause'])

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

const isCurrentEpisodeBuffering = computed(() => {
  return podcastStore.currentEpisode?.uuid === props.episode.uuid && podcastStore.isBuffering
})

function handlePlayClick() {
  if (isCurrentlyPlaying.value) {
    // Pause si cet épisode est en cours de lecture
    $emit('pause')
  } else {
    // Play sinon
    $emit('play', props.episode)
  }
}

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
  background: var(--color-background-neutral);
  border-radius: var(--radius-04);
  padding: var(--space-03);
  cursor: pointer;
  transition: background var(--transition-fast);
}

.episode-card:hover {
  background: var(--color-background-strong);
}

.card-image {
  width: 128px;
  height: 128px;
  flex-shrink: 0;
  border-radius: var(--radius-03);
  overflow: hidden;
}

.card-image img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.card-content {
  flex: 1;
  min-width: 0;
  display: flex;
  gap: var(--space-03);
  align-items: center;
}

.content-info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
}

.episode-name {
  color: var(--color-text);
  margin: 0;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.podcast-name {
  color: var(--color-brand);
  margin: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.episode-meta {
  color: var(--color-text-secondary);
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
  background: var(--color-background-strong);
  border-radius: var(--radius-01);
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  background: var(--color-brand);
  transition: width var(--transition-fast);
}

.time-remaining {
  color: var(--color-brand);
  margin-top: var(--space-01);
  display: block;
}

@media (max-aspect-ratio: 4/3) {
  .content-info {
    gap: 0;
  }

  .card-image {
    width: 64px;
    height: 64px;
  }
  .episode-meta {
    display: none;
  }
}
</style>
