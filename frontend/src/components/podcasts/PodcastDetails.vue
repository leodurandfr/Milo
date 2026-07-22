<template>
  <div class="podcast-details">
    <div class="transition-container">
      <!-- Skeleton state -->
      <transition name="content-fade">
        <SkeletonPodcastDetails v-if="loading" key="loading" />
      </transition>

      <!-- Real content -->
      <transition name="content-fade">
        <div v-if="!loading && podcast" key="loaded" class="details-content">
          <DetailHeader
            :image-src="podcast.image_url"
            :fallback="podcastPlaceholder"
            :title="podcast.name"
            :subtitle="podcast.publisher || podcast.author"
            :subtitle-meta="`${podcast.total_episodes} ${t('podcasts.episodesCount2')}`"
            :show-play="false"
            :show-shuffle="false"
          >
            <template #actions>
              <Button v-if="!podcast.is_subscribed" variant="brand" size="small" @click="handleSubscribe">
                {{ t('podcasts.subscribe') }}
              </Button>
              <Button v-else variant="on-dark" size="small" @click="handleUnsubscribe">
                {{ t('podcasts.unsubscribe') }}
              </Button>
            </template>
          </DetailHeader>

          <!-- Episodes list -->
          <div class="episodes-section">
            <h3 class="heading-2">{{ t('podcasts.episodesTitle') }}</h3>
            <div class="episodes-list">
              <EpisodeCard
                v-for="episode in allEpisodes"
                :key="episode.uuid"
                :episode="episode"
                @select="$emit('select-episode', episode.uuid)"
                @play="$emit('play-episode', episode)"
              />
            </div>

            <!-- Load more button -->
            <div v-if="hasMoreEpisodes" class="load-more-container">
              <Button
                variant="brand"
                :loading="loadingMore"
                @click="loadMoreEpisodes"
              >
                {{ t('podcasts.loadMoreEpisodes') }}
              </Button>
            </div>
          </div>
        </div>
      </transition>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { usePodcastStore } from '@/stores/podcastStore'
import { useI18n } from '@/services/i18n'
import { apiCall } from '@/services/apiCall'
import { useAsyncData } from '@/composables/useAsyncData'
import DetailHeader from '@/components/audio/DetailHeader.vue'
import EpisodeCard from './EpisodeCard.vue'
import Button from '@/components/ui/Button.vue'
import SkeletonPodcastDetails from './SkeletonPodcastDetails.vue'
import podcastPlaceholder from '@/assets/podcasts/podcast-placeholder.jpg'

const { t } = useI18n()

const props = defineProps({
  uuid: {
    type: String,
    required: true
  }
})

const emit = defineEmits(['play-episode', 'select-episode'])
const podcastStore = usePodcastStore()

const podcast = ref(null)
const currentPage = ref(1)
const loadingMore = ref(false)
const allEpisodes = ref([])

const hasMoreEpisodes = computed(() => {
  if (!podcast.value) return false
  return allEpisodes.value.length < podcast.value.total_episodes
})

const { loading, execute: loadPodcast } = useAsyncData(async () => {
  currentPage.value = 1
  const result = await apiCall.get(`/api/podcast/series/${props.uuid}`, {
    category: 'podcast',
    message: 'Error loading podcast details',
    params: { page: 1, limit: 25 },
  })
  if (!result.ok) return
  podcast.value = result.data
  allEpisodes.value = result.data.episodes || []
  podcastStore.enrichEpisodesWithProgress(allEpisodes.value)
})

async function handleSubscribe() {
  if (!podcast.value) return

  const result = await apiCall.post('/api/podcast/subscriptions', {
    uuid: props.uuid,
    name: podcast.value.name || '',
    image_url: podcast.value.image_url || '',
    children_hash: podcast.value.children_hash || '',
    itunes_id: podcast.value.itunes_id ?? null,
  }, {
    category: 'podcast',
    message: 'Error subscribing',
  })
  if (!result.ok) return
  podcast.value.is_subscribed = true
  podcastStore.addSubscription({
    uuid: props.uuid,
    name: podcast.value.name || '',
    image_url: podcast.value.image_url || '',
    itunes_id: podcast.value.itunes_id ?? null,
  })
}

async function handleUnsubscribe() {
  if (!podcast.value) return

  const result = await apiCall.delete(`/api/podcast/subscriptions/${props.uuid}`, {
    category: 'podcast',
    message: 'Error unsubscribing',
  })
  if (!result.ok) return
  podcast.value.is_subscribed = false
  podcastStore.removeSubscription(props.uuid)
}

async function loadMoreEpisodes() {
  if (loadingMore.value || !hasMoreEpisodes.value) return

  loadingMore.value = true
  currentPage.value++

  const result = await apiCall.get(`/api/podcast/series/${props.uuid}`, {
    category: 'podcast',
    message: 'Error loading more episodes',
    params: { page: currentPage.value, limit: 25 },
  })

  if (result.ok) {
    const newEpisodes = result.data.episodes || []
    podcastStore.enrichEpisodesWithProgress(newEpisodes)
    allEpisodes.value = [...allEpisodes.value, ...newEpisodes]
  } else {
    currentPage.value-- // Rollback on error
  }
  loadingMore.value = false
}

watch(() => props.uuid, loadPodcast, { immediate: false })
onMounted(loadPodcast)
</script>

<style scoped>
.podcast-details {
  display: flex;
  flex-direction: column;
}

.transition-container {
  display: grid;
  grid-template-columns: 1fr;
}

.transition-container > * {
  grid-column: 1;
  grid-row: 1;
}

.content-fade-enter-active {
  transition: opacity var(--transition-normal);
}

.content-fade-leave-active {
  transition: opacity var(--transition-normal-leave);
}

.content-fade-enter-from,
.content-fade-leave-to {
  opacity: 0;
}

.details-content {
  display: flex;
  flex-direction: column;
  gap: var(--space-06);
  min-width: 0;
}

.episodes-section h3 {
  margin: 0 0 var(--space-03);
}

.episodes-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.load-more-container {
  display: flex;
  justify-content: center;
  padding: var(--space-04) 0;
  margin-top: var(--space-02);
}
</style>
