<template>
  <div class="episode-details">
    <LoadingSpinner v-if="loading" />

    <template v-else-if="episode">
      <div class="details-header">
        <img :src="episode.image_url" :alt="episode.name" class="episode-image" />
        <div class="header-info">
          <h2 class="heading-1">{{ episode.name }}</h2>
          <p class="podcast-link text-body" @click="$emit('select-podcast', episode.podcast?.uuid)">
            {{ episode.podcast?.name }}
          </p>
          <p class="meta text-mono">
            {{ formattedDuration }} • {{ formattedDate }}
          </p>
          <div v-if="showBadges" class="badges">
            <span v-if="episode.episode_type && episode.episode_type !== 'FULL'" class="badge text-mono">
              {{ episode.episode_type }}
            </span>
            <span v-if="episode.season_number" class="badge text-mono">
              S{{ episode.season_number }} E{{ episode.episode_number }}
            </span>
          </div>
          <Button
            variant="toggle"
            :leftIcon="buttonIcon"
            @click="handlePlayClick"
          >
            {{ buttonText }}
          </Button>
        </div>
      </div>

      <div class="description">
        <h3 class="heading-2">{{ t('podcasts.description') }}</h3>
        <p class="text-body-small">{{ episode.description }}</p>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { usePodcastStore } from '@/stores/podcastStore'
import { useI18n } from '@/services/i18n'
import Button from '@/components/ui/Button.vue'
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue'

const { t } = useI18n()

const props = defineProps({
  uuid: {
    type: String,
    required: true
  }
})

const emit = defineEmits(['back', 'play-episode', 'select-podcast'])

const podcastStore = usePodcastStore()
const loading = ref(false)
const episode = ref(null)

const hasProgress = computed(() => {
  if (!episode.value?.uuid) return false
  // Read from reactive progress cache instead of static props
  const progress = podcastStore.getEpisodeProgress(episode.value.uuid)
  return progress && progress.position > 0
})

const formattedDuration = computed(() => {
  const seconds = episode.value?.duration || 0
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  if (h > 0) return `${h}h ${m}min`
  return `${m} min`
})

const formattedDate = computed(() => {
  if (!episode.value?.date_published) return ''
  const date = new Date(episode.value.date_published * 1000)
  return date.toLocaleDateString('fr-FR', {
    day: 'numeric',
    month: 'long',
    year: 'numeric'
  })
})

const showBadges = computed(() => {
  if (!episode.value) return false
  const hasEpisodeTypeBadge = episode.value.episode_type && episode.value.episode_type !== 'FULL'
  const hasSeasonBadge = episode.value.season_number
  return hasEpisodeTypeBadge || hasSeasonBadge
})

const isCurrentEpisode = computed(() => {
  if (!episode.value?.uuid) return false
  return podcastStore.currentEpisode?.uuid === episode.value.uuid
})

const isCurrentlyPlaying = computed(() => {
  return isCurrentEpisode.value && podcastStore.isPlaying
})

const buttonText = computed(() => {
  if (isCurrentlyPlaying.value) {
    return t('podcasts.pause')
  }
  return hasProgress.value ? t('podcasts.resume') : t('podcasts.listen')
})

const buttonIcon = computed(() => {
  return isCurrentlyPlaying.value ? 'pause' : 'play'
})

async function loadEpisode() {
  loading.value = true
  try {
    const response = await fetch(`/api/podcast/episode/${props.uuid}`)
    episode.value = await response.json()

    // Enrich with progress cache if available
    if (episode.value && episode.value.playback_progress) {
      podcastStore.enrichEpisodesWithProgress([episode.value])
    }
  } catch (error) {
    console.error('Error loading episode:', error)
  } finally {
    loading.value = false
  }
}

function handlePlayClick() {
  if (isCurrentlyPlaying.value) {
    podcastStore.pause()
  } else {
    emit('play-episode', episode.value)
  }
}

watch(() => props.uuid, loadEpisode, { immediate: false })
onMounted(loadEpisode)
</script>

<style scoped>
.episode-details {
  display: flex;
  flex-direction: column;
  gap: var(--space-05);
}

.details-header {
  display: flex;
  gap: var(--space-04);
}

.episode-image {
  width: 240px;
  height: 240px;
  border-radius: var(--radius-04);
  object-fit: cover;
  flex-shrink: 0;
}

.header-info {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.header-info h2 {
  margin: 0;
}

.podcast-link {
  color: var(--color-accent);
  cursor: pointer;
  margin: 0;
}

.meta {
  color: var(--color-text-secondary);
  margin: 0;
}

.badges {
  display: flex;
  gap: var(--space-02);
}

.badge {
  padding: var(--space-01) var(--space-02);
  background: var(--color-background-neutral);
  border-radius: var(--radius-02);
  color: var(--color-text-secondary);
}

.description h3 {
  margin: 0 0 var(--space-03);
}

.description p {
  line-height: 1.6;
  margin: 0;
}
</style>
