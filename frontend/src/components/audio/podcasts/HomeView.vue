<template>
  <div class="home-view">
    <!-- New episodes from subscriptions (Bloc 1) -->
    <section v-if="latestSubscriptionEpisodes.length > 0" class="section">
      <h3 class="section-title heading-2">{{ t('podcasts.newEpisodesFromSubscriptions') }}</h3>
      <div class="episodes-list">
        <EpisodeCard v-for="episode in latestSubscriptionEpisodes.slice(0, 4)" :key="episode.uuid" :episode="episode"
          @select="$emit('select-episode', episode.uuid)" @play="$emit('play-episode', episode)" @pause="handlePause" />
      </div>
    </section>

    <!-- Top Podcasts (Bloc 2) -->
    <section class="section">
      <h3 class="section-title heading-2">{{ t('podcasts.topPodcasts') }}</h3>
      <LoadingSpinner v-if="loadingTopCharts" />
      <div v-else class="podcasts-grid">
        <PodcastCard v-for="(podcast, index) in topCharts.slice(0, 6)" :key="podcast.uuid" :podcast="podcast"
          :position="index + 1" @select="$emit('select-podcast', podcast.uuid)" />
      </div>
    </section>

    <!-- Browse by Genre (Bloc 3) -->
    <section class="section">
      <h3 class="section-title heading-2">{{ t('podcasts.browseByGenre') }}</h3>
      <div class="genres-grid">
        <div v-for="genre in mainGenres" :key="genre.value" class="genre-card" @click="browseGenre(genre.value)">
          <span class="genre-emoji">{{ genre.emoji }}</span>
          <span>{{ genre.label }}</span>
        </div>
      </div>
    </section>

    <!-- Top Episodes (Bloc 4) -->
    <section class="section">
      <h3 class="section-title heading-2">{{ t('podcasts.topEpisodes') }}</h3>
      <LoadingSpinner v-if="loadingTopEpisodes" />
      <div v-else class="episodes-list">
        <EpisodeCard v-for="episode in topEpisodes.slice(0, 6)" :key="episode.uuid" :episode="episode"
          @select="$emit('select-episode', episode.uuid)" @play="$emit('play-episode', episode)" @pause="handlePause" />
      </div>
    </section>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { usePodcastStore } from '@/stores/podcastStore'
import { useI18n } from '@/services/i18n'
import PodcastCard from './PodcastCard.vue'
import EpisodeCard from './EpisodeCard.vue'
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue'

const emit = defineEmits(['select-podcast', 'select-episode', 'play-episode', 'browse-genre'])
const { t } = useI18n()

const podcastStore = usePodcastStore()

const loadingTopCharts = ref(false)
const loadingTopEpisodes = ref(false)
const topCharts = ref([])
const topEpisodes = ref([])
const latestSubscriptionEpisodes = ref([])

const mainGenres = computed(() => [
  { value: 'PODCASTSERIES_NEWS', label: t('podcasts.genres.news'), emoji: '📰' },
  { value: 'PODCASTSERIES_COMEDY', label: t('podcasts.genres.comedy'), emoji: '😂' },
  { value: 'PODCASTSERIES_TRUE_CRIME', label: t('podcasts.genres.trueCrime'), emoji: '🔍' },
  { value: 'PODCASTSERIES_TECHNOLOGY', label: t('podcasts.genres.tech'), emoji: '💻' },
  { value: 'PODCASTSERIES_SPORTS', label: t('podcasts.genres.sports'), emoji: '🏆' },
  { value: 'PODCASTSERIES_EDUCATION', label: t('podcasts.genres.education'), emoji: '🎓' },
  { value: 'PODCASTSERIES_BUSINESS', label: t('podcasts.genres.business'), emoji: '💼' },
  { value: 'PODCASTSERIES_HEALTH_AND_FITNESS', label: t('podcasts.genres.health'), emoji: '❤️' }
])

function browseGenre(genreValue) {
  // Find genre label
  const genre = mainGenres.value.find(g => g.value === genreValue)
  if (genre) {
    emit('browse-genre', genreValue, genre.label)
  }
}

async function handlePause() {
  await podcastStore.pause()
}

async function loadData() {
  const settings = podcastStore.settings
  const country = settings.defaultCountry || 'FRANCE'

  // Load latest episodes from subscriptions (Bloc 1)
  try {
    const latestResponse = await fetch('/api/podcast/subscriptions/latest-episodes?limit=10')
    const latestData = await latestResponse.json()
    latestSubscriptionEpisodes.value = podcastStore.enrichEpisodesWithProgress(latestData.results || [])
  } catch (error) {
    console.error('Error loading subscription episodes:', error)
  }

  // Load top podcasts (Bloc 2)
  loadingTopCharts.value = true
  try {
    const response = await fetch(`/api/podcast/discover/top-charts/${country}?content_type=PODCASTSERIES&limit=10`)
    const data = await response.json()
    topCharts.value = data.results || []
  } catch (error) {
    console.error('Error loading top charts:', error)
  } finally {
    loadingTopCharts.value = false
  }

  // Load top episodes (Bloc 4)
  loadingTopEpisodes.value = true
  try {
    const response = await fetch(`/api/podcast/discover/top-charts/${country}?content_type=PODCASTEPISODE&limit=10`)
    const data = await response.json()
    topEpisodes.value = podcastStore.enrichEpisodesWithProgress(data.results || [])
  } catch (error) {
    console.error('Error loading top episodes:', error)
  } finally {
    loadingTopEpisodes.value = false
  }
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

.podcasts-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--space-03);
}

.episodes-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.genres-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
  gap: var(--space-02);
}

.genre-emoji {
  font-size: 24px;
}

.genre-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-02);
  padding: var(--space-04);
  background: var(--color-background-neutral);
  border-radius: var(--radius-04);
  cursor: pointer;
  transition: background var(--transition-fast), transform var(--transition-fast);
}



.genre-card span {
  font-size: var(--font-size-sm);
  font-weight: var(--font-weight-medium);
  color: var(--color-text);
  text-align: center;
}

/* Mobile: Responsive adaptations */
@media (max-aspect-ratio: 4/3) {
  .podcasts-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}
</style>
