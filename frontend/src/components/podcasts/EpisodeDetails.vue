<template>
  <div class="episode-details">
    <div class="swap-stack">
      <Transition name="fade-slide">
        <SkeletonEpisodeDetails v-if="loading" key="loading" />

        <div v-else-if="episode" key="loaded" class="details-content">
          <DetailHeader
            :image-src="episode.image_url || episode.podcast?.image_url"
            :fallback="podcastPlaceholder"
            :title="episode.name"
            :subtitle="episode.podcast?.name"
            :subtitle-clickable="!!episode.podcast"
            :subtitle-meta="subtitleMeta"
            :show-play="false"
            :show-shuffle="false"
            @select-artist="handleSelectPodcast"
          >
            <template #actions>
              <IconButton :icon="isCurrentlyPlaying ? 'pause' : 'play'" variant="brand" size="medium"
                :loading="isCurrentEpisodeBuffering" @click="handlePlayClick" />
            </template>
          </DetailHeader>

          <div class="description-block">
            <h3 class="text-mono-medium description-title">{{ t('podcasts.description') }}</h3>
            <p class="text-body-medium">{{ episode.description }}</p>
          </div>
        </div>
      </Transition>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { usePodcastStore } from '@/stores/podcastStore'
import { useEpisodePlaybackStatus } from '@/composables/useEpisodePlaybackStatus'
import { useI18n } from '@/services/i18n'
import { apiCall } from '@/services/apiCall'
import { useAsyncData } from '@/composables/useAsyncData'
import DetailHeader from '@/components/audio/DetailHeader.vue'
import IconButton from '@/components/ui/IconButton.vue'
import SkeletonEpisodeDetails from './SkeletonEpisodeDetails.vue'
import { podcastPlaceholder } from '@/constants/placeholders'

const { t } = useI18n()

const props = defineProps({
  uuid: {
    type: String,
    required: true
  }
})

const emit = defineEmits(['play-episode', 'select-podcast'])

const podcastStore = usePodcastStore()
const episode = ref(null)

const {
  isCurrentlyPlaying,
  isCurrentEpisodeBuffering,
  formattedDate,
  statusLabel,
  pause,
} = useEpisodePlaybackStatus(episode)

const subtitleMeta = computed(() => {
  const parts = [statusLabel.value];
  if (formattedDate.value) parts.push(formattedDate.value);
  return parts.join(' · ');
})

const { loading, execute: loadEpisode } = useAsyncData(async () => {
  const result = await apiCall.get(`/api/podcast/episode/${props.uuid}`, {
    category: 'podcast',
    message: 'Error loading episode details',
  })
  if (!result.ok) return
  episode.value = result.data
  podcastStore.enrichEpisodesWithProgress([episode.value])
}, { logTag: 'podcast' })

async function handlePlayClick() {
  if (isCurrentlyPlaying.value) {
    await pause()
  } else {
    emit('play-episode', episode.value)
  }
}

function handleSelectPodcast() {
  if (episode.value?.podcast) emit('select-podcast', episode.value.podcast)
}

watch(() => props.uuid, loadEpisode, { immediate: false })
onMounted(loadEpisode)
</script>

<style scoped>
.episode-details {
  display: flex;
  flex-direction: column;
}

.details-content {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
  min-width: 0;
}

.description-block {
  background: var(--color-surface);
  border-radius: var(--radius-04);
  padding: var(--space-04);
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
  min-width: 0;
  box-sizing: border-box;
}

.description-title {
  color: var(--color-text-secondary);
  margin: 0;
}

.description-block p {
  margin: 0;
}
</style>
