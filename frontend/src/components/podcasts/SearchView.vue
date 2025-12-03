<template>
  <div class="search-view">
    <!-- Filters -->
    <div class="filters-bar">
      <InputText v-model="searchTerm" :placeholder="t('podcasts.searchPlaceholder')" icon="search"
        @update:modelValue="onSearchInput" />
      <Dropdown v-model="searchFilters.language" :options="languageOptions" />
      <Dropdown v-model="searchFilters.genre" :options="genreOptions" />
      <Dropdown v-model="searchFilters.duration" :options="durationOptions" />
    </div>

    <!-- Results with transitions -->
    <div class="results" ref="resultsContainer">
      <Transition name="fade-slide" mode="out-in">
        <!-- Loading state -->
        <MessageContent v-if="loading" key="loading" loading :title="t('podcasts.loading')" />

        <!-- Search results -->
        <div v-else-if="hasSearched && (searchResults.podcasts.length > 0 || searchResults.episodes.length > 0)" key="results" class="results-content">
          <!-- Podcasts results -->
          <section v-if="searchResults.podcasts.length > 0" class="section">
            <h2 class="heading-2">
              {{ t('podcasts.podcastsTitle') }}
            </h2>
            <div class="podcasts-grid">
              <PodcastCard v-for="podcast in searchResults.podcasts" :key="podcast.uuid" :podcast="podcast"
                @select="$emit('select-podcast', podcast.uuid)" />
            </div>
            <div v-if="searchCurrentPage.podcasts < searchPagination.podcasts.pages" class="load-more-container">
              <Button variant="brand" :loading="searchLoadingMore.podcasts" @click="loadMorePodcasts">
                {{ t('podcasts.loadMorePodcasts') }}
              </Button>
            </div>
          </section>

          <!-- Episodes results -->
          <section v-if="searchResults.episodes.length > 0" class="section">
            <h2 class="heading-2">
              {{ t('podcasts.recentEpisodesTitle') }}
            </h2>
            <div class="episodes-list">
              <EpisodeCard v-for="episode in searchResults.episodes" :key="episode.uuid" :episode="episode"
                @select="$emit('select-episode', episode.uuid)" @play="$emit('play-episode', episode)"
                @pause="handlePause" @select-podcast="(podcast) => $emit('select-podcast', podcast)" />
            </div>
            <div v-if="searchCurrentPage.episodes < searchPagination.episodes.pages" class="load-more-container">
              <Button variant="brand" :loading="searchLoadingMore.episodes" @click="loadMoreEpisodes">
                {{ t('podcasts.loadMoreEpisodes') }}
              </Button>
            </div>
          </section>
        </div>

        <!-- No results -->
        <MessageContent
          v-else-if="hasSearched"
          key="no-results"
          icon="search"
          :title="lastSearchTerm ? t('podcasts.noResultsFor', { query: lastSearchTerm }) : t('podcasts.noResults')"
        />

        <!-- Initial state -->
        <MessageContent v-else key="initial" icon="search" :title="t('podcasts.searchPrompt')" />
      </Transition>
    </div>
  </div>
</template>

<script setup>
import { watch } from 'vue'
import { storeToRefs } from 'pinia'
import { usePodcastStore } from '@/stores/podcastStore'
import { useI18n } from '@/services/i18n'
import PodcastCard from './PodcastCard.vue'
import EpisodeCard from './EpisodeCard.vue'
import InputText from '@/components/ui/InputText.vue'
import Button from '@/components/ui/Button.vue'
import Dropdown from '@/components/ui/Dropdown.vue'
import MessageContent from '@/components/ui/MessageContent.vue'

const emit = defineEmits(['select-podcast', 'select-episode', 'play-episode'])
const podcastStore = usePodcastStore()
const { t } = useI18n()

// Get reactive refs from store (persisted across navigation)
const {
  searchTerm,
  lastSearchTerm,
  searchFilters,
  searchResults,
  searchPagination,
  searchCurrentPage,
  hasSearched,
  searchLoading: loading,
  searchLoadingMore
} = storeToRefs(podcastStore)

async function handlePause() {
  await podcastStore.pause()
}

// Filter options (static constants)
const languageOptions = [
  { value: '', label: t('podcasts.languageFilter.all') },
  { value: 'ENGLISH', label: t('podcasts.languageFilter.english') },
  { value: 'FRENCH', label: t('podcasts.languageFilter.french') },
  { value: 'SPANISH', label: t('podcasts.languageFilter.spanish') },
  { value: 'GERMAN', label: t('podcasts.languageFilter.german') },
  { value: 'ITALIAN', label: t('podcasts.languageFilter.italian') },
  { value: 'PORTUGUESE', label: t('podcasts.languageFilter.portuguese') },
  { value: 'CHINESE', label: t('podcasts.languageFilter.chinese') },
  { value: 'JAPANESE', label: t('podcasts.languageFilter.japanese') },
  { value: 'KOREAN', label: t('podcasts.languageFilter.korean') },
  { value: 'HINDI', label: t('podcasts.languageFilter.hindi') },
  { value: 'ARABIC', label: t('podcasts.languageFilter.arabic') },
  { value: 'DUTCH_FLEMISH', label: t('podcasts.languageFilter.dutch') },
  { value: 'POLISH', label: t('podcasts.languageFilter.polish') },
  { value: 'RUSSIAN', label: t('podcasts.languageFilter.russian') },
  { value: 'SWEDISH', label: t('podcasts.languageFilter.swedish') },
  { value: 'TURKISH', label: t('podcasts.languageFilter.turkish') }
]

const durationOptions = [
  { value: '', label: t('podcasts.duration.label') },
  { value: 'short', label: t('podcasts.duration.short') },
  { value: 'medium', label: t('podcasts.duration.medium') },
  { value: 'long', label: t('podcasts.duration.long') }
]

const genreOptions = [
  { value: '', label: t('podcasts.genreFilter.all') },
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

// Debounce timers (local UI state)
let filterDebounceTimer = null
let searchDebounceTimer = null

// Helper to check if all filters and search term are empty
function isEmptyState() {
  return !searchTerm.value &&
    !searchFilters.value.language &&
    !searchFilters.value.duration &&
    !searchFilters.value.genre
}

// Handle search input with debounce
function onSearchInput() {
  // Reset to initial state if everything is empty
  if (isEmptyState()) {
    hasSearched.value = false
    searchResults.value = { podcasts: [], episodes: [] }
    return
  }

  // Debounce search
  if (searchDebounceTimer) {
    clearTimeout(searchDebounceTimer)
  }
  searchDebounceTimer = setTimeout(() => {
    performSearch()
  }, 400)
}

// Watch filters and auto-trigger search when they change
watch(
  () => [searchFilters.value.language, searchFilters.value.duration, searchFilters.value.genre],
  () => {
    // Reset to initial state if everything is empty
    if (isEmptyState()) {
      hasSearched.value = false
      searchResults.value = { podcasts: [], episodes: [] }
      return
    }

    // Auto-search if filters are active
    if (filterDebounceTimer) {
      clearTimeout(filterDebounceTimer)
    }
    filterDebounceTimer = setTimeout(() => {
      performSearch()
    }, 400)
  }
)

async function performSearch() {
  loading.value = true

  try {
    const params = new URLSearchParams({
      term: searchTerm.value,
      sort_by: 'EXACTNESS',
      safe_mode: 'false',
      limit: '25',
      page: '1'
    })

    // Add duration filter if selected
    if (searchFilters.value.duration) {
      const durationMap = {
        short: { min: 0, max: 900 },      // 0-15 min
        medium: { min: 900, max: 2700 },  // 15-45 min
        long: { min: 2700, max: 999999 }  // 45+ min
      }
      const duration = durationMap[searchFilters.value.duration]
      params.append('duration_min', duration.min.toString())
      params.append('duration_max', duration.max.toString())
    }

    // Add genre filter if selected
    if (searchFilters.value.genre) {
      params.append('genres', searchFilters.value.genre)
    }

    // Add language filter if selected
    if (searchFilters.value.language) {
      params.append('languages', searchFilters.value.language)
    }

    const response = await fetch(`/api/podcast/search?${params}`)
    const data = await response.json()

    // Store results via store action
    podcastStore.setSearchResults(data.podcasts, data.episodes, data.pagination)
  } catch (error) {
    console.error('Error searching:', error)
    searchResults.value = { podcasts: [], episodes: [] }
  } finally {
    loading.value = false
  }
}

async function loadMorePodcasts() {
  if (searchLoadingMore.value.podcasts || searchCurrentPage.value.podcasts >= searchPagination.value.podcasts.pages) return

  searchLoadingMore.value.podcasts = true

  try {
    const nextPage = searchCurrentPage.value.podcasts + 1
    const params = new URLSearchParams({
      term: searchTerm.value,
      sort_by: 'EXACTNESS',
      safe_mode: 'false',
      limit: '25',
      page: nextPage.toString()
    })

    if (searchFilters.value.duration) {
      const durationMap = {
        short: { min: 0, max: 900 },
        medium: { min: 900, max: 2700 },
        long: { min: 2700, max: 999999 }
      }
      const duration = durationMap[searchFilters.value.duration]
      params.append('duration_min', duration.min.toString())
      params.append('duration_max', duration.max.toString())
    }

    if (searchFilters.value.genre) {
      params.append('genres', searchFilters.value.genre)
    }

    if (searchFilters.value.language) {
      params.append('languages', searchFilters.value.language)
    }

    const response = await fetch(`/api/podcast/search?${params}`)
    const data = await response.json()

    podcastStore.appendSearchResults('podcasts', data.podcasts || [])
  } catch (error) {
    console.error('Error loading more podcasts:', error)
  } finally {
    searchLoadingMore.value.podcasts = false
  }
}

async function loadMoreEpisodes() {
  if (searchLoadingMore.value.episodes || searchCurrentPage.value.episodes >= searchPagination.value.episodes.pages) return

  searchLoadingMore.value.episodes = true

  try {
    const nextPage = searchCurrentPage.value.episodes + 1
    const params = new URLSearchParams({
      term: searchTerm.value,
      sort_by: 'EXACTNESS',
      safe_mode: 'false',
      limit: '25',
      page: nextPage.toString()
    })

    if (searchFilters.value.duration) {
      const durationMap = {
        short: { min: 0, max: 900 },
        medium: { min: 900, max: 2700 },
        long: { min: 2700, max: 999999 }
      }
      const duration = durationMap[searchFilters.value.duration]
      params.append('duration_min', duration.min.toString())
      params.append('duration_max', duration.max.toString())
    }

    if (searchFilters.value.genre) {
      params.append('genres', searchFilters.value.genre)
    }

    if (searchFilters.value.language) {
      params.append('languages', searchFilters.value.language)
    }

    const response = await fetch(`/api/podcast/search?${params}`)
    const data = await response.json()

    podcastStore.appendSearchResults('episodes', data.episodes || [])
  } catch (error) {
    console.error('Error loading more episodes:', error)
  } finally {
    searchLoadingMore.value.episodes = false
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
  min-width: 180px;
}



.results {
  display: flex;
  flex-direction: column;
  gap: var(--space-06);
}

.results-content {
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
  gap: var(--space-02);
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

/* Mobile: scroll horizontal au lieu de wrap vertical */
@media (max-aspect-ratio: 4/3) {
  .filters-bar {
    flex-wrap: nowrap;
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;

    /* Full-bleed: compense le padding du parent AudioSourceLayout */
    margin-left: calc(-1 * var(--space-05));
    margin-right: calc(-1 * var(--space-05));
    padding-left: var(--space-05);
    padding-right: var(--space-05);

    /* Masquer la scrollbar */
    scrollbar-width: none;
    -ms-overflow-style: none;
  }

  .filters-bar::-webkit-scrollbar {
    display: none;
  }

  .filters-bar > * {
    flex-shrink: 0;
  }
}
</style>
