<template>
  <div v-press class="episode-card" :class="{ clickable, contrast }" @click="handleCardClick">
    <LazyImage
      :src="episode.image_url || episode.podcast?.image_url"
      :fallback="episodePlaceholder"
      :alt="episode.name"
      lazy
      class="card-image"
    />

    <div class="card-content">
      <div class="content-info">
        <h4 class="episode-name heading-3">{{ episode.name }}</h4>
        <p v-if="podcastName" class="podcast-name text-mono clickable-link" @click.stop="handlePodcastClick">{{ podcastName }}</p>

        <div class="episode-meta text-mono">
          <span class="duration">
            <template v-if="isCurrentlyPlaying">{{ t('podcasts.nowPlaying') }}</template>
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
          @pointerdown.stop @click.stop="emit('complete', episode)" />
        <IconButton :icon="isCurrentlyPlaying ? 'pause' : 'play'" :variant="contrast ? 'on-dark' : 'background-strong'" size="medium"
          :loading="isCurrentEpisodeBuffering" @pointerdown.stop @click.stop="handlePlayClick" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { usePodcastStore } from '@/stores/podcastStore'
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore'
import { useI18n } from '@/services/i18n'
import IconButton from '@/components/ui/IconButton.vue'
import LazyImage from '@/components/ui/LazyImage.vue'
import episodePlaceholder from '@/assets/podcasts/podcast-placeholder.jpg'

const { t, currentLanguage } = useI18n()

const LANGUAGE_TO_LOCALE = {
  english: 'en-US',
  french: 'fr-FR',
  spanish: 'es-ES',
  german: 'de-DE',
  italian: 'it-IT',
  portuguese: 'pt-BR',
  chinese: 'zh-CN',
  hindi: 'hi-IN'
}

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

const emit = defineEmits(['select', 'play', 'complete', 'select-podcast'])

const podcastStore = usePodcastStore()
const unifiedStore = useUnifiedAudioStore()

function handleCardClick() {
  if (props.clickable) {
    emit('select', props.episode)
  }
}

function handlePodcastClick() {
  if (props.episode.podcast) {
    emit('select-podcast', props.episode.podcast)
  }
}
const podcastName = computed(() => {
  return props.episode.podcast?.name || ''
})

// Check if this episode is the current one (playing or paused)
const isCurrentEpisode = computed(() => {
  return podcastStore.currentEpisode?.uuid === props.episode.uuid
})

const isPodcastActive = computed(() => unifiedStore.systemState.active_source === 'podcast')

const isCurrentlyPlaying = computed(() => {
  return isCurrentEpisode.value && isPodcastActive.value &&
    (unifiedStore.systemState.metadata?.is_playing || false)
})

const isCurrentEpisodeBuffering = computed(() => {
  return podcastStore.isEpisodePending(props.episode.uuid) ||
    (isCurrentEpisode.value && isPodcastActive.value &&
      (unifiedStore.systemState.metadata?.is_buffering || false))
})

async function handlePlayClick() {
  if (isCurrentlyPlaying.value) {
    await podcastStore.pause()
  } else {
    emit('play', props.episode)
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

  const locale = LANGUAGE_TO_LOCALE[currentLanguage.value] || 'en-US'
  const day = date.getDate()
  const month = date.toLocaleDateString(locale, { month: 'short' }).replace('.', '')
  const capitalized = month.charAt(0).toUpperCase() + month.slice(1)
  return `${day} ${capitalized}`
}


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
  width: 128px;
  height: 128px;
  flex-shrink: 0;
  border-radius: var(--radius-02);
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
    width: 96px;
    height: 96px;
  }
  .episode-meta {
    display: flex;
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
