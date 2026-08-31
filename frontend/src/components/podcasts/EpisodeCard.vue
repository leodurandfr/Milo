<template>
  <div v-press class="episode-card" :class="{ clickable }" @click="handleCardClick">
    <LazyImage
      :src="episode.image_url || episode.podcast?.image_url"
      :fallback="podcastPlaceholder"
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
            <template v-else-if="isCompleted">{{ t('podcasts.alreadyListened') }}</template>
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
        <IconButton v-if="showCompleteButton" icon="close" variant="background-strong" size="medium"
          @pointerdown.stop @click.stop="emit('complete', episode)" />
        <IconButton :icon="isCurrentlyPlaying ? 'pause' : 'play'" variant="background-strong" size="medium"
          :loading="isCurrentEpisodeBuffering" @pointerdown.stop @click.stop="handlePlayClick" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, toRef } from 'vue'
import { useEpisodePlaybackStatus } from '@/composables/useEpisodePlaybackStatus'
import { useI18n } from '@/services/i18n'
import IconButton from '@/components/ui/IconButton.vue'
import LazyImage from '@/components/ui/LazyImage.vue'
import { podcastPlaceholder } from '@/constants/placeholders'

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
  }
})

const emit = defineEmits(['select', 'play', 'complete', 'select-podcast'])

const {
  isCurrentlyPlaying,
  isCurrentEpisodeBuffering,
  isCompleted,
  hasProgress,
  timeRemaining,
  formattedDuration,
  formattedDate,
  pause,
} = useEpisodePlaybackStatus(toRef(props, 'episode'))

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

async function handlePlayClick() {
  if (isCurrentlyPlaying.value) {
    await pause()
  } else {
    emit('play', props.episode)
  }
}
</script>

<style scoped>
.episode-card {
  display: flex;
  gap: var(--space-03);
  background: var(--color-background-neutral);
  border-radius: var(--radius-04);
  padding: var(--space-03) var(--space-04) var(--space-03) var(--space-03);
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
</style>
