<template>
  <div class="home-view">
    <!-- New episodes from subscriptions (Bloc 1) - Only show if user has subscriptions -->
    <section v-if="hasSubscriptions" class="section">
      <h2 class="section-title heading-2">{{ t('podcasts.newEpisodesFromSubscriptions') }}</h2>
      <div class="transition-container">
        <transition name="content-fade">
          <div v-if="loadingSubscriptions" key="loading-sub" class="episodes-list">
            <SkeletonEpisodeCard v-for="i in 4" :key="`skeleton-sub-${i}`" />
          </div>
        </transition>

        <transition name="content-fade">
          <div v-if="!loadingSubscriptions && latestSubscriptionEpisodes.length > 0" key="loaded-sub" class="episodes-list">
            <EpisodeCard
              v-for="episode in latestSubscriptionEpisodes.slice(0, 4)"
              :key="episode.uuid"
              :episode="episode"
              @select="$emit('select-episode', episode.uuid)"
              @play="$emit('play-episode', episode)"

              @select-podcast="(podcast) => $emit('select-podcast', podcast)"
            />
          </div>
        </transition>

        <!-- Empty state with MessageContent when subscribed but no new episodes -->
        <transition name="content-fade">
          <MessageContent v-if="!loadingSubscriptions && latestSubscriptionEpisodes.length === 0" key="empty-sub" icon="heartOff" :title="t('podcasts.noNewEpisodes')" />
        </transition>
      </div>
    </section>

    <!-- Top Podcasts (Bloc 2) -->
    <section class="section">
      <h2 class="section-title heading-2">{{ t('podcasts.topPodcasts') }}</h2>
      <div class="transition-container">
        <transition name="content-fade">
          <div v-if="loadingTopCharts" key="loading-podcasts" class="podcasts-grid">
            <SkeletonPodcastCard v-for="i in 6" :key="`skeleton-podcast-${i}`" />
          </div>
        </transition>

        <!-- The catalog could not be loaded, whether Podcast Index did not answer or the
             request itself failed — the subscriptions block above is unaffected -->
        <transition name="content-fade">
          <MessageContent
            v-if="!loadingTopCharts && topChartsApiError"
            key="api-error-podcasts"
            icon="network"
            :title="t('podcasts.catalogUnavailable')"
            :subtitle="t('podcasts.catalogUnavailableHint')"
            :cta-label="t('podcasts.retry')"
            cta-variant="background-strong"
            :cta-click="loadData"
          />
        </transition>

        <transition name="content-fade">
          <div v-if="!loadingTopCharts && !topChartsApiError" key="loaded-podcasts" class="podcasts-grid">
            <PodcastCard
              v-for="(podcast, index) in topCharts.slice(0, 6)"
              :key="podcast.itunes_id || podcast.uuid"
              :podcast="podcast"
              :position="index + 1"
              :isLoading="isPodcastLoading(podcast)"
              @select="$emit('select-podcast', podcast)"
            />
          </div>
        </transition>
      </div>
    </section>

    <!-- Browse by Genre (Bloc 3) -->
    <section class="section">
      <h2 class="section-title heading-2">{{ t('podcasts.browseByGenre') }}</h2>
      <div class="genres-grid">
        <GenreCard
          v-for="genre in mainGenres"
          :key="genre.value"
          :value="genre.value"
          :label="genre.label"
          @click="browseGenre(genre.value)"
        />
      </div>
    </section>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { usePodcastStore } from '@/stores/podcastStore'
import { useI18n } from '@/services/i18n'
import { apiCall } from '@/services/apiCall'
import { logger } from '@/services/logger'
import PodcastCard from './PodcastCard.vue'
import EpisodeCard from './EpisodeCard.vue'
import GenreCard from './GenreCard.vue'
import SkeletonPodcastCard from './SkeletonPodcastCard.vue'
import SkeletonEpisodeCard from './SkeletonEpisodeCard.vue'
import MessageContent from '@/components/ui/MessageContent.vue'

const emit = defineEmits(['select-podcast', 'select-episode', 'play-episode', 'browse-genre'])
const { t } = useI18n()

// loadingPodcastId is set by the parent (PodcastSource) while it resolves an
// iTunes chart entry's feedId — used to show a spinner on the tapped card.
const props = defineProps({
  loadingPodcastId: {
    type: [String, Number],
    default: null
  }
})

const podcastStore = usePodcastStore()

const loadingTopCharts = ref(true)
const loadingSubscriptions = ref(true)
const topCharts = ref([])
const topChartsApiError = ref(false)

// True while the tapped iTunes chart entry is being resolved to a feedId.
function isPodcastLoading(podcast) {
  if (!props.loadingPodcastId) return false
  return podcast.itunes_id === props.loadingPodcastId || podcast.uuid === props.loadingPodcastId
}

// Use store's computed for hasSubscriptions (preloaded in App.vue)
const hasSubscriptions = computed(() => podcastStore.hasSubscriptions)
const latestSubscriptionEpisodes = computed(() => podcastStore.latestSubscriptionEpisodes)

const mainGenres = computed(() => [
  { value: 'PODCASTSERIES_COMEDY', label: t('podcasts.genres.comedy') },
  { value: 'PODCASTSERIES_SOCIETY_AND_CULTURE', label: t('podcasts.genres.society_and_culture') },
  { value: 'PODCASTSERIES_NEWS', label: t('podcasts.genres.news') },
  { value: 'PODCASTSERIES_TRUE_CRIME', label: t('podcasts.genres.true_crime') },
  { value: 'PODCASTSERIES_BUSINESS', label: t('podcasts.genres.business') },
  { value: 'PODCASTSERIES_EDUCATION', label: t('podcasts.genres.education') },
  { value: 'PODCASTSERIES_HEALTH_AND_FITNESS', label: t('podcasts.genres.health_and_fitness') },
  { value: 'PODCASTSERIES_SPORTS', label: t('podcasts.genres.sports') },
  { value: 'PODCASTSERIES_ARTS', label: t('podcasts.genres.arts') },
  { value: 'PODCASTSERIES_SCIENCE', label: t('podcasts.genres.science') },
  { value: 'PODCASTSERIES_TV_AND_FILM', label: t('podcasts.genres.tv_and_film') },
  { value: 'PODCASTSERIES_MUSIC', label: t('podcasts.genres.music') }
])

function browseGenre(genreValue) {
  const genre = mainGenres.value.find(g => g.value === genreValue)
  if (genre) {
    emit('browse-genre', genreValue, genre.label)
  }
}

async function loadData() {
  // Note: Country/language is automatically derived from user settings on the backend

  // Load subscriptions via store (cached, preloaded in App.vue)
  loadingSubscriptions.value = true
  try {
    await podcastStore.loadSubscriptions()
  } catch (error) {
    logger.error('podcast', 'Error loading subscription episodes:', error)
  } finally {
    loadingSubscriptions.value = false
  }

  // Load top podcasts (Bloc 2) - backend derives country from user's language setting
  loadingTopCharts.value = true
  const podcastsResult = await apiCall.get('/api/podcast/discover/top-charts', {
    category: 'podcast',
    message: 'Error loading top charts',
    params: { limit: 10 },
  })
  if (podcastsResult.ok) {
    const data = podcastsResult.data
    if (data.api_error) {
      topChartsApiError.value = true
      topCharts.value = []
    } else {
      topChartsApiError.value = false
      topCharts.value = data.results || []
    }
  } else {
    // The request itself failed (backend 500, or a 502 while milo-backend restarts).
    // Without this arm the grid renders empty and loadData — the retry — is unreachable.
    topChartsApiError.value = true
    topCharts.value = []
  }
  loadingTopCharts.value = false
}

onMounted(() => {
  loadData()
})
</script>

<style scoped>
.home-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-07);
}

.section {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

.section-title {
  color: var(--color-text);
  margin: 0;
}

/* Transition container for overlay effect */
.transition-container {
  display: grid;
  grid-template-columns: 1fr;
}

.transition-container > * {
  grid-column: 1;
  grid-row: 1;
}

.podcasts-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--space-02);
}

.episodes-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.genres-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--space-02);
}

/* Content fade transition (skeleton to real content) */
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

/* Card fade-in transition */
.fade-in-cards-enter-active {
  transition: opacity var(--transition-normal), transform var(--transition-normal);
}

.fade-in-cards-enter-from {
  opacity: 0;
  transform: translateY(8px);
}

.fade-in-cards-enter-to {
  opacity: 1;
  transform: translateY(0);
}

/* Empty state message */
.empty-message {
  color: var(--color-text-secondary);
  text-align: center;
  padding: var(--space-07) var(--space-05);
  margin: 0;
}

/* Mobile: Responsive adaptations */
@media (max-aspect-ratio: 4/3) {
  .podcasts-grid {
    grid-template-columns: repeat(2, 1fr);
  }
  .genres-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}
</style>
