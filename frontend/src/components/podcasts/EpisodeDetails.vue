<template>
  <div class="episode-details">
    <div class="swap-stack">
      <Transition name="fade-slide">
        <SkeletonEpisodeDetails v-if="loading" key="loading" class="swap-skeleton" />

        <div v-else-if="episode" key="loaded" class="details-content">
          <DetailHeader
            :image-src="episode.image_url || episode.podcast?.image_url"
            :fallback="podcastPlaceholder"
            :title="episode.name"
            :subtitle="episode.podcast?.name"
            :subtitle-clickable="!!episode.podcast"
            :subtitle-meta="subtitleMeta"
            :playing="header.playing.value"
            :play-loading="header.loading.value"
            @play="header.play"
            @pause="header.pause"
            @select-artist="handleSelectPodcast"
          />

          <div class="description-block">
            <h3 class="text-mono-medium description-title">{{ t('podcasts.description') }}</h3>
            <p class="text-body-medium">{{ episode.description }}</p>
          </div>
        </div>

        <!-- 404: the episode is no longer in its feed. Anything else (503 when
             the feed could not be read, a dropped link) is passing. Either way
             the page says so and offers to ask again, rather than sit blank. -->
        <MessageContent
          v-else-if="failure"
          :key="`failed-${failure}`"
          :icon="failure === 'absent' ? 'podcast' : 'network'"
          :title="failure === 'absent' ? t('podcasts.episodeNotAvailable') : t('podcasts.catalogUnavailable')"
          :subtitle="failure === 'absent' ? t('podcasts.episodeNotAvailableHint') : t('podcasts.catalogUnavailableHint')"
          :cta-label="t('podcasts.retry')"
          cta-variant="control"
          :cta-click="loadEpisode"
        />
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
import { useDetailPlayback } from '@/composables/useDetailPlayback'
import { onPageReturn } from '@/composables/useNavigationStack'
import { logger } from '@/services/logger'
import DetailHeader from '@/components/audio/DetailHeader.vue'
import SkeletonEpisodeDetails from './SkeletonEpisodeDetails.vue'
import MessageContent from '@/components/ui/MessageContent.vue'
import { podcastPlaceholder } from '@/constants/placeholders'

const { t } = useI18n()

const props = defineProps({
  uuid: {
    type: String,
    required: true
  }
})

const emit = defineEmits(['select-podcast'])

const podcastStore = usePodcastStore()
const episode = ref(null)
// Why the last load brought no episode: 'absent' (404) or 'transient'.
const failure = ref(null)

const { formattedDate, statusLabel } = useEpisodePlaybackStatus(episode)

const header = useDetailPlayback(
  'podcast',
  () => podcastStore.currentEpisode?.uuid === props.uuid,
  () => podcastStore.play(props.uuid).then(() => true, (error) => {
    logger.error('podcast', 'Error playing episode', error)
    return false
  }),
)

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
  if (!result.ok) {
    episode.value = null
    failure.value = result.error?.status === 404 ? 'absent' : 'transient'
    return
  }
  failure.value = null
  episode.value = result.data
  podcastStore.enrichEpisodesWithProgress([episode.value])
}, { logTag: 'podcast' })

function handleSelectPodcast() {
  if (episode.value?.podcast) emit('select-podcast', episode.value.podcast)
}

watch(() => props.uuid, loadEpisode, { immediate: false })
onMounted(loadEpisode)
// Shown again (KeepAlive) with nothing loaded: ask again.
onPageReturn(() => { if (!episode.value && !loading.value) loadEpisode() })
</script>

<style scoped>
.episode-details {
  display: flex;
  flex-direction: column;
  /* The header's buttons end where the episode cards' do. */
  --detail-header-inset-end: var(--space-04);
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
