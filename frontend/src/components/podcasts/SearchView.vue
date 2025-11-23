<template>
  <div class="search-view">
    <!-- Filters -->
    <div class="filters-bar">
      <InputText v-model="searchTerm" :placeholder="t('podcasts.searchPlaceholder')" icon="search"
        @keyup.enter="performSearch" />
      <Dropdown v-model="selectedDuration" :options="durationOptions" />
      <Dropdown v-model="selectedGenre" :options="genreOptions" />
    </div>

    <!-- Results -->
    <div class="results" ref="resultsContainer">
      <LoadingSpinner v-if="loading" />

      <template v-else-if="hasSearched">
        <!-- Podcasts results -->
        <section v-if="podcastResults.length > 0" class="section">
          <h2 class="heading-2">
            {{ t('podcasts.podcastsCount', { count: pagination.podcasts.total }) }}
          </h2>
          <div class="podcasts-grid">
            <PodcastCard v-for="podcast in podcastResults" :key="podcast.uuid" :podcast="podcast"
              @select="$emit('select-podcast', podcast.uuid)" />
          </div>
          <div v-if="currentPodcastPage < pagination.podcasts.pages" class="load-more-container">
            <Button variant="primary" :loading="loadingMorePodcasts" @click="loadMorePodcasts">
              {{ t('podcasts.loadMorePodcasts') }}
            </Button>
          </div>
        </section>

        <!-- Episodes results -->
        <section v-if="episodeResults.length > 0" class="section">
          <h2 class="heading-2">
            {{ t('podcasts.episodesCount', { count: pagination.episodes.total }) }}
          </h2>
          <div class="episodes-list">
            <EpisodeCard v-for="episode in episodeResults" :key="episode.uuid" :episode="episode"
              @select="$emit('select-episode', episode.uuid)" @play="$emit('play-episode', episode)"
              @pause="handlePause" />
          </div>
          <div v-if="currentEpisodePage < pagination.episodes.pages" class="load-more-container">
            <Button variant="primary" :loading="loadingMoreEpisodes" @click="loadMoreEpisodes">
              {{ t('podcasts.loadMoreEpisodes') }}
            </Button>
          </div>
        </section>

        <!-- No results -->
        <div v-if="podcastResults.length === 0 && episodeResults.length === 0" class="message-wrapper">
          <div class="message-content">
            <SvgIcon name="search" :size="48" />
            <p class="text-mono">
              {{ lastSearchTerm ? t('podcasts.noResultsFor', { query: lastSearchTerm }) : t('podcasts.noResults') }}
            </p>
          </div>
        </div>
      </template>

      <!-- Initial state -->
      <div v-else class="message-wrapper">
        <div class="message-content">
          <SvgIcon name="search" :size="48" />
          <p class="text-mono">{{ t('podcasts.searchPrompt') }}</p>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import { usePodcastStore } from '@/stores/podcastStore'
import { useI18n } from '@/services/i18n'
import PodcastCard from './PodcastCard.vue'
import EpisodeCard from './EpisodeCard.vue'
import InputText from '@/components/ui/InputText.vue'
import Button from '@/components/ui/Button.vue'
import Dropdown from '@/components/ui/Dropdown.vue'
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue'
import SvgIcon from '@/components/ui/SvgIcon.vue'

const emit = defineEmits(['select-podcast', 'select-episode', 'play-episode'])
const podcastStore = usePodcastStore()
const { t } = useI18n()

async function handlePause() {
  await podcastStore.pause()
}

const searchTerm = ref('')
const lastSearchTerm = ref('')
const loading = ref(false)
const hasSearched = ref(false)

// Filters
const selectedDuration = ref('all')
const selectedGenre = ref('all')

const durationOptions = [
  { value: 'all', label: t('podcasts.duration.label') },
  { value: 'short', label: t('podcasts.duration.short') },
  { value: 'medium', label: t('podcasts.duration.medium') },
  { value: 'long', label: t('podcasts.duration.long') }
]

const genreOptions = [
  { value: 'all', label: t('podcasts.genreFilter.all') },
  { value: 'PODCASTSERIES_ARTS', label: t('podcasts.genres.arts') },
  { value: 'PODCASTSERIES_BUSINESS', label: t('podcasts.genres.business') },
  { value: 'PODCASTSERIES_COMEDY', label: t('podcasts.genres.comedy') },
  { value: 'PODCASTSERIES_EDUCATION', label: t('podcasts.genres.education') },
  { value: 'PODCASTSERIES_FICTION', label: t('podcasts.genres.fiction') },
  { value: 'PODCASTSERIES_GOVERNMENT', label: t('podcasts.genres.government') },
  { value: 'PODCASTSERIES_HEALTH_AND_FITNESS', label: t('podcasts.genres.health_and_fitness') },
  { value: 'PODCASTSERIES_HISTORY', label: t('podcasts.genres.history') },
  { value: 'PODCASTSERIES_KIDS_AND_FAMILY', label: t('podcasts.genres.kids_and_family') },
  { value: 'PODCASTSERIES_LEISURE', label: t('podcasts.genres.leisure') },
  { value: 'PODCASTSERIES_MUSIC', label: t('podcasts.genres.music') },
  { value: 'PODCASTSERIES_NEWS', label: t('podcasts.genres.news') },
  { value: 'PODCASTSERIES_RELIGION_AND_SPIRITUALITY', label: t('podcasts.genres.religion_and_spirituality') },
  { value: 'PODCASTSERIES_SCIENCE', label: t('podcasts.genres.science') },
  { value: 'PODCASTSERIES_SOCIETY_AND_CULTURE', label: t('podcasts.genres.society_and_culture') },
  { value: 'PODCASTSERIES_SPORTS', label: t('podcasts.genres.sports') },
  { value: 'PODCASTSERIES_TECHNOLOGY', label: t('podcasts.genres.technology') },
  { value: 'PODCASTSERIES_TRUE_CRIME', label: t('podcasts.genres.true_crime') },
  { value: 'PODCASTSERIES_TV_AND_FILM', label: t('podcasts.genres.tv_and_film') }
]

// Results and pagination
const podcastResults = ref([])
const episodeResults = ref([])
const pagination = ref({
  podcasts: { total: 0, pages: 0 },
  episodes: { total: 0, pages: 0 }
})
const currentPodcastPage = ref(1)
const currentEpisodePage = ref(1)
const loadingMorePodcasts = ref(false)
const loadingMoreEpisodes = ref(false)
const resultsContainer = ref(null)

// Debounce timer for filter changes
const filterDebounceTimer = ref(null)

// Watch filters and auto-trigger search when they change
watch([selectedDuration, selectedGenre], () => {
  // Only auto-search if user has interacted OR filters are active
  const hasActiveFilters = selectedDuration.value !== 'all' ||
    selectedGenre.value !== 'all'

  if (hasSearched.value || hasActiveFilters) {
    // Debounce to avoid rapid API calls
    if (filterDebounceTimer.value) {
      clearTimeout(filterDebounceTimer.value)
    }
    filterDebounceTimer.value = setTimeout(() => {
      performSearch()
    }, 300)  // 300ms debounce
  }
})

async function performSearch() {
  loading.value = true
  currentPodcastPage.value = 1
  currentEpisodePage.value = 1
  podcastResults.value = []
  episodeResults.value = []

  hasSearched.value = true
  lastSearchTerm.value = searchTerm.value

  try {
    const params = new URLSearchParams({
      term: searchTerm.value,
      sort_by: 'EXACTNESS',
      safe_mode: 'false',
      limit: '25',
      page: '1'
    })

    // Add duration filter if selected
    if (selectedDuration.value !== 'all') {
      const durationMap = {
        short: { min: 0, max: 900 },      // 0-15 min
        medium: { min: 900, max: 2700 },  // 15-45 min
        long: { min: 2700, max: 999999 }  // 45+ min
      }
      const duration = durationMap[selectedDuration.value]
      params.append('duration_min', duration.min.toString())
      params.append('duration_max', duration.max.toString())
    }

    // Add genre filter if selected
    if (selectedGenre.value !== 'all') {
      params.append('genres', selectedGenre.value)
    }

    const response = await fetch(`/api/podcast/search?${params}`)
    const data = await response.json()

    podcastResults.value = data.podcasts || []
    episodeResults.value = podcastStore.enrichEpisodesWithProgress(data.episodes || [])

    pagination.value = data.pagination || {
      podcasts: { total: 0, pages: 0 },
      episodes: { total: 0, pages: 0 }
    }
  } catch (error) {
    console.error('Error searching:', error)
    podcastResults.value = []
    episodeResults.value = []
  } finally {
    loading.value = false
  }
}

async function loadMorePodcasts() {
  if (loadingMorePodcasts.value || currentPodcastPage.value >= pagination.value.podcasts.pages) return

  loadingMorePodcasts.value = true
  currentPodcastPage.value++

  try {
    const params = new URLSearchParams({
      term: searchTerm.value,
      sort_by: 'EXACTNESS',
      safe_mode: 'false',
      limit: '25',
      page: currentPodcastPage.value.toString()
    })

    if (selectedDuration.value !== 'all') {
      const durationMap = {
        short: { min: 0, max: 900 },
        medium: { min: 900, max: 2700 },
        long: { min: 2700, max: 999999 }
      }
      const duration = durationMap[selectedDuration.value]
      params.append('duration_min', duration.min.toString())
      params.append('duration_max', duration.max.toString())
    }

    if (selectedGenre.value !== 'all') {
      params.append('genres', selectedGenre.value)
    }

    const response = await fetch(`/api/podcast/search?${params}`)
    const data = await response.json()

    podcastResults.value = [...podcastResults.value, ...(data.podcasts || [])]
  } catch (error) {
    console.error('Error loading more podcasts:', error)
  } finally {
    loadingMorePodcasts.value = false
  }
}

async function loadMoreEpisodes() {
  if (loadingMoreEpisodes.value || currentEpisodePage.value >= pagination.value.episodes.pages) return

  loadingMoreEpisodes.value = true
  currentEpisodePage.value++

  try {
    const params = new URLSearchParams({
      term: searchTerm.value,
      sort_by: 'EXACTNESS',
      safe_mode: 'false',
      limit: '25',
      page: currentEpisodePage.value.toString()
    })

    if (selectedDuration.value !== 'all') {
      const durationMap = {
        short: { min: 0, max: 900 },
        medium: { min: 900, max: 2700 },
        long: { min: 2700, max: 999999 }
      }
      const duration = durationMap[selectedDuration.value]
      params.append('duration_min', duration.min.toString())
      params.append('duration_max', duration.max.toString())
    }

    if (selectedGenre.value !== 'all') {
      params.append('genres', selectedGenre.value)
    }

    const response = await fetch(`/api/podcast/search?${params}`)
    const data = await response.json()

    const newEpisodes = podcastStore.enrichEpisodesWithProgress(data.episodes || [])
    episodeResults.value = [...episodeResults.value, ...newEpisodes]
  } catch (error) {
    console.error('Error loading more episodes:', error)
  } finally {
    loadingMoreEpisodes.value = false
  }
}

</script>

<style scoped>
.search-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

.search-bar {
  display: flex;
  width: 100%;
}

.filters-bar {
  display: flex;
  gap: var(--space-02);
  align-items: center;
  flex-wrap: wrap;
}

.filters-bar>* {
  flex: 1;
  min-width: 140px;
}

.results {
  display: flex;
  flex-direction: column;
  gap: var(--space-06);
}

.section {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

.section-title {
  font-size: var(--font-size-lg);
  font-weight: var(--font-weight-semibold);
  color: var(--color-text);
  margin: 0;
}

.podcasts-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: var(--space-03);
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

.message-wrapper {
  background: var(--color-background-neutral);
  border-radius: var(--radius-04);
}

.message-content {
  display: flex;
  min-height: 232px;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  gap: var(--space-04);
  padding: var(--space-05);
}

.message-content .text-mono,
.message-content .heading-2 {
  text-align: center;
  color: var(--color-text-secondary);
}

.message-content .text-body-small {
  text-align: center;
}

/* Mobile: increase height for better visibility */
@media (max-aspect-ratio: 4/3) {
  .message-content {
    min-height: 364px;
  }
}
</style>
