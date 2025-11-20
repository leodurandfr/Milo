<template>
  <div class="search-view">
    <!-- Single line search bar with filters -->
    <div class="search-bar">
      <InputText
        v-model="searchTerm"
        placeholder="Rechercher des podcasts ou épisodes..."
        icon="search"
        @keyup.enter="performSearch"
      />
      <Dropdown v-model="selectedLanguage" :options="languageOptions" />
      <Dropdown v-model="selectedDuration" :options="durationOptions" />
    </div>

    <!-- Results -->
    <div class="results" ref="resultsContainer" @scroll="handleScroll">
      <LoadingSpinner v-if="loading" />

      <template v-else-if="hasSearched">
        <!-- Podcasts results -->
        <section v-if="podcastResults.length > 0" class="section">
          <h3 class="section-title">
            Podcasts ({{ pagination.podcasts.total }})
          </h3>
          <div class="podcasts-grid">
            <PodcastCard
              v-for="podcast in podcastResults"
              :key="podcast.uuid"
              :podcast="podcast"
              @select="$emit('select-podcast', podcast.uuid)"
            />
          </div>
        </section>

        <!-- Episodes results -->
        <section v-if="episodeResults.length > 0" class="section">
          <h3 class="section-title">
            Épisodes ({{ pagination.episodes.total }})
          </h3>
          <div class="episodes-list">
            <EpisodeCard
              v-for="episode in episodeResults"
              :key="episode.uuid"
              :episode="episode"
              @select="$emit('select-episode', episode.uuid)"
              @play="$emit('play-episode', episode)"
              @pause="handlePause"
            />
          </div>
        </section>

        <!-- Loading more indicator -->
        <div v-if="loadingMore" class="loading-more">
          <LoadingSpinner />
          <p>Chargement de plus de résultats...</p>
        </div>

        <!-- No results -->
        <div
          v-if="podcastResults.length === 0 && episodeResults.length === 0"
          class="empty-state"
        >
          <Icon name="search" :size="48" />
          <p>Aucun résultat pour "{{ lastSearchTerm }}"</p>
        </div>
      </template>

      <!-- Initial state -->
      <div v-else class="empty-state">
        <Icon name="search" :size="48" />
        <p>Recherchez des podcasts ou épisodes</p>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { usePodcastStore } from '@/stores/podcastStore'
import PodcastCard from './PodcastCard.vue'
import EpisodeCard from './EpisodeCard.vue'
import InputText from '@/components/ui/InputText.vue'
import Button from '@/components/ui/Button.vue'
import Dropdown from '@/components/ui/Dropdown.vue'
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue'
import Icon from '@/components/ui/Icon.vue'

const emit = defineEmits(['select-podcast', 'select-episode', 'play-episode'])
const podcastStore = usePodcastStore()

async function handlePause() {
  await podcastStore.pause()
}

const searchTerm = ref('')
const lastSearchTerm = ref('')
const loading = ref(false)
const loadingMore = ref(false)
const hasSearched = ref(false)

// Filters
const selectedLanguage = ref('all')
const selectedDuration = ref('all')

const languageOptions = [
  { value: 'all', label: 'Toutes les langues' },
  { value: 'FRENCH', label: 'Français' },
  { value: 'ENGLISH', label: 'Anglais' },
  { value: 'SPANISH', label: 'Espagnol' },
  { value: 'GERMAN', label: 'Allemand' },
  { value: 'ITALIAN', label: 'Italien' },
  { value: 'PORTUGUESE', label: 'Portugais' },
  { value: 'CHINESE', label: 'Chinois' },
  { value: 'HINDI', label: 'Hindi' }
]

const durationOptions = [
  { value: 'all', label: 'Durée' },
  { value: 'short', label: 'Court (< 15 min)' },
  { value: 'medium', label: 'Moyen (15-45 min)' },
  { value: 'long', label: 'Long (> 45 min)' }
]

// Results and pagination
const podcastResults = ref([])
const episodeResults = ref([])
const pagination = ref({
  podcasts: { total: 0, pages: 0 },
  episodes: { total: 0, pages: 0 }
})
const currentPage = ref(1)
const resultsContainer = ref(null)

async function performSearch(resetPage = true) {
  if (!searchTerm.value.trim()) return

  if (resetPage) {
    loading.value = true
    currentPage.value = 1
    podcastResults.value = []
    episodeResults.value = []
  } else {
    loadingMore.value = true
  }

  hasSearched.value = true
  lastSearchTerm.value = searchTerm.value

  try {
    const params = new URLSearchParams({
      term: searchTerm.value,
      sort_by: 'POPULARITY',
      safe_mode: 'false',
      limit: '25',
      page: currentPage.value.toString()
    })

    // Add language filter if selected
    if (selectedLanguage.value !== 'all') {
      params.append('languages', selectedLanguage.value)
    }

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

    const response = await fetch(`/api/podcast/search?${params}`)
    const data = await response.json()

    if (resetPage) {
      podcastResults.value = data.podcasts || []
      episodeResults.value = data.episodes || []
    } else {
      // Append results for infinite scroll
      podcastResults.value = [...podcastResults.value, ...(data.podcasts || [])]
      episodeResults.value = [...episodeResults.value, ...(data.episodes || [])]
    }

    pagination.value = data.pagination || {
      podcasts: { total: 0, pages: 0 },
      episodes: { total: 0, pages: 0 }
    }
  } catch (error) {
    console.error('Error searching:', error)
    if (resetPage) {
      podcastResults.value = []
      episodeResults.value = []
    }
  } finally {
    loading.value = false
    loadingMore.value = false
  }
}

function handleScroll(event) {
  if (loadingMore.value || loading.value) return

  const element = event.target
  const scrollBottom = element.scrollHeight - element.scrollTop - element.clientHeight

  // Load more when within 200px of bottom
  if (scrollBottom < 200) {
    const maxPages = Math.max(pagination.value.podcasts.pages, pagination.value.episodes.pages)

    if (currentPage.value < maxPages && currentPage.value < 20) {
      currentPage.value++
      performSearch(false)
    }
  }
}
</script>

<style scoped>
.search-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

.search-bar {
  display: flex;
  gap: var(--space-02);
  align-items: center;
}

.search-bar > :first-child {
  flex: 1;
  min-width: 0;
}

.results {
  display: flex;
  flex-direction: column;
  gap: var(--space-06);
  max-height: 70vh;
  overflow-y: auto;
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

.loading-more {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: var(--space-04);
  gap: var(--space-02);
}

.loading-more p {
  font-size: var(--font-size-sm);
  color: var(--color-text-muted);
  margin: 0;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: var(--space-08);
  color: var(--color-text-muted);
  text-align: center;
}

.empty-state p {
  margin: var(--space-02) 0 0;
}
</style>
