<template>
  <div class="episode-card" :class="{ clickable, contrast }" @click="handleCardClick">
    <div class="card-image">
      <img
        ref="imgRef"
        :src="imageUrl"
        :alt="episode.name"
        loading="lazy"
        @load="handleImageLoad"
        @error="handleImageError"
        :class="{ loaded: imageLoaded }"
        class="main-image"
      />
      <img
        v-if="!imageLoaded && !imageError"
        :src="episodePlaceholder"
        class="placeholder-image"
        alt=""
      />
    </div>

    <div class="card-content">
      <div class="content-info">
        <h4 class="episode-name heading-3">{{ episode.name }}</h4>
        <p v-if="podcastName" class="podcast-name text-mono clickable-link" @click.stop="handlePodcastClick">{{ podcastName }}</p>

        <div class="episode-meta text-mono">
          <span class="duration">
            <template v-if="isCurrentlyPlaying">{{ $t('podcasts.nowPlaying') }}</template>
            <template v-else-if="hasProgress">{{ timeRemaining }}</template>
            <template v-else>{{ formattedDuration }}</template>
          </span>
          <template v-if="formattedDate">
            <span class="separator">•</span>
            <span class="date">{{ formattedDate }}</span>
          </template>
        </div>
      </div>

      <div class="card-actions">
        <IconButton v-if="showCompleteButton" icon="close" :variant="contrast ? 'on-dark' : 'background-strong'" size="medium"
          @click.stop="$emit('complete', episode)" />
        <IconButton :icon="isCurrentlyPlaying ? 'pause' : 'play'" :variant="contrast ? 'on-dark' : 'background-strong'" size="medium"
          :loading="isCurrentEpisodeBuffering" @click.stop="handlePlayClick" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, onMounted } from 'vue'
import { usePodcastStore } from '@/stores/podcastStore'
import { useI18n } from '@/services/i18n'
import IconButton from '@/components/ui/IconButton.vue'
import episodePlaceholder from '@/assets/podcasts/podcast-placeholder.jpg'

const { t } = useI18n()

const props = defineProps({
  episode: {
    type: Object,
    required: true
  },
  showCompleteButton: {
    type: Boolean,
    default: false
  },
  clickable: {
    type: Boolean,
    default: true
  },
  contrast: {
    type: Boolean,
    default: false
  }
})

const $emit = defineEmits(['select', 'play', 'pause', 'complete', 'select-podcast'])

const podcastStore = usePodcastStore()

function handleCardClick() {
  if (props.clickable) {
    $emit('select', props.episode)
  }
}

function handlePodcastClick() {
  if (props.episode.podcast) {
    $emit('select-podcast', props.episode.podcast)
  }
}
const imageError = ref(false)
const imageLoaded = ref(false)
const imgRef = ref(null)

const imageUrl = computed(() => {
  if (imageError.value) return episodePlaceholder
  return props.episode.image_url ||
    props.episode.podcast?.image_url ||
    episodePlaceholder
})

const podcastName = computed(() => {
  return props.episode.podcast?.name || ''
})

// Check if this episode is the current one (playing or paused)
const isCurrentEpisode = computed(() => {
  return podcastStore.currentEpisode?.uuid === props.episode.uuid
})

const isCurrentlyPlaying = computed(() => {
  return isCurrentEpisode.value && podcastStore.isPlaying
})

const isCurrentEpisodeBuffering = computed(() => {
  // Show loading if: pending (optimistic, immediate) OR currently buffering
  return podcastStore.isEpisodePending(props.episode.uuid) ||
         (isCurrentEpisode.value && podcastStore.isBuffering)
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
  // If this is the current episode, read from store (real-time)
  if (isCurrentEpisode.value) {
    return podcastStore.currentPosition > 0
  }
  // Otherwise, read from reactive progress cache (updated via WebSocket)
  const progress = podcastStore.getEpisodeProgress(props.episode.uuid)
  return progress && progress.position > 0
})

const progressPercent = computed(() => {
  // If this is the current episode, use live data
  if (isCurrentEpisode.value) {
    if (!podcastStore.currentDuration) return 0
    return (podcastStore.currentPosition / podcastStore.currentDuration) * 100
  }
  // Otherwise, use reactive progress cache (updated via WebSocket)
  const progress = podcastStore.getEpisodeProgress(props.episode.uuid)
  if (!progress || !progress.duration) return 0
  return (progress.position / progress.duration) * 100
})

const timeRemaining = computed(() => {
  let remaining

  // If this is the current episode, use live data
  if (isCurrentEpisode.value) {
    remaining = podcastStore.currentDuration - podcastStore.currentPosition
  } else {
    // Otherwise, use reactive progress cache (updated via WebSocket)
    const progress = podcastStore.getEpisodeProgress(props.episode.uuid)
    if (!progress) return ''
    remaining = progress.duration - progress.position
  }

  // Check if episode is completed (less than 5 seconds remaining)
  if (remaining <= 5) {
    return t('podcasts.episodeCompleted')
  }

  return formatDuration(remaining) + ' ' + t('podcasts.remaining')
})

const formattedDuration = computed(() => {
  // If this is the current episode, use live duration from store
  if (isCurrentEpisode.value) {
    return formatDuration(podcastStore.currentDuration || 0)
  }
  // Otherwise, use episode's static duration
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

  if (days === 0) return t('podcasts.today')
  if (days === 1) return t('podcasts.yesterday')
  if (days < 7) return t('podcasts.daysAgo', { count: days })
  if (days < 30) return t('podcasts.weeksAgo', { count: Math.floor(days / 7) })
  if (days < 365) return t('podcasts.monthsAgo', { count: Math.floor(days / 30) })
  return t('podcasts.yearsAgo', { count: Math.floor(days / 365) })
}

function handleImageError() {
  imageError.value = true
}

function handleImageLoad() {
  imageLoaded.value = true
}

onMounted(() => {
  if (imgRef.value && imgRef.value.complete && imgRef.value.naturalHeight !== 0) {
    imageLoaded.value = true
  }
})
</script>

<style scoped>
.episode-card {
  display: flex;
  gap: var(--space-03);
  background: var(--color-background-neutral);
  border-radius: var(--radius-04);
  padding: var(--space-03) var(--space-04) var(--space-03) var(--space-03);
  transition: all var(--transition-fast);
}

.episode-card.clickable {
  cursor: pointer;
}


.card-image {
  position: relative;
  width: 128px;
  height: 128px;
  flex-shrink: 0;
  border-radius: var(--radius-02);
  overflow: hidden;
}

.card-image img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  position: absolute;
  inset: 0;
}

.card-image .main-image {
  opacity: 0;
  transition: opacity var(--transition-normal);
  z-index: 1;
}

.card-image .main-image.loaded {
  opacity: 1;
}

.card-image .placeholder-image {
  opacity: 1;
  z-index: 0;
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

.card-actions {
  display: flex;
  gap: var(--space-02);
  align-items: center;
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
  display: -webkit-box;
  -webkit-line-clamp: 1;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.podcast-name.clickable-link {
  cursor: pointer;
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

/* === CONTRAST VARIANT === */
.episode-card.contrast {
  background: var(--color-background-contrast);
}

.episode-card.contrast .episode-name {
  color: var(--color-text-contrast);
}

.episode-card.contrast .podcast-name {
  color: var(--color-brand);
}

.episode-card.contrast .episode-meta {
  color: var(--color-text-contrast-50);
}
</style>
