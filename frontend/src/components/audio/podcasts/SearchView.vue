<template>
  <div class="search-view">
    <!-- Search input -->
    <div class="search-bar">
      <InputText
        v-model="searchTerm"
        placeholder="Rechercher des podcasts ou épisodes..."
        icon="search"
        @keyup.enter="performSearch"
      />
      <Button variant="toggle" @click="performSearch">
        Rechercher
      </Button>
    </div>

    <!-- Filters toggle -->
    <div class="filters-toggle">
      <Button variant="secondary" size="small" @click="showFilters = !showFilters">
        <Icon name="settings" :size="16" />
        Filtres
        <Icon :name="showFilters ? 'caretUp' : 'caretDown'" :size="12" />
      </Button>
    </div>

    <!-- Filters panel -->
    <div v-if="showFilters" class="filters-panel">
      <div class="filter-group">
        <label>Tri</label>
        <Dropdown v-model="sortBy" :options="sortOptions" />
      </div>
      <div class="filter-group">
        <label>Mode sécurisé</label>
        <Toggle v-model="safeMode" />
      </div>
    </div>

    <!-- Results -->
    <div class="results">
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
            />
          </div>
        </section>

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
import PodcastCard from './PodcastCard.vue'
import EpisodeCard from './EpisodeCard.vue'
import InputText from '@/components/ui/InputText.vue'
import Button from '@/components/ui/Button.vue'
import Dropdown from '@/components/ui/Dropdown.vue'
import Toggle from '@/components/ui/Toggle.vue'
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue'
import Icon from '@/components/ui/Icon.vue'

const emit = defineEmits(['select-podcast', 'select-episode', 'play-episode'])

const searchTerm = ref('')
const lastSearchTerm = ref('')
const showFilters = ref(false)
const loading = ref(false)
const hasSearched = ref(false)

const sortBy = ref('EXACTNESS')
const safeMode = ref(false)

const sortOptions = [
  { value: 'EXACTNESS', label: 'Pertinence' },
  { value: 'POPULARITY', label: 'Popularité' }
]

const podcastResults = ref([])
const episodeResults = ref([])
const pagination = ref({
  podcasts: { total: 0, pages: 0 },
  episodes: { total: 0, pages: 0 }
})

async function performSearch() {
  if (!searchTerm.value.trim()) return

  loading.value = true
  hasSearched.value = true
  lastSearchTerm.value = searchTerm.value

  try {
    const params = new URLSearchParams({
      term: searchTerm.value,
      sort_by: sortBy.value,
      safe_mode: safeMode.value.toString(),
      limit: '15'
    })

    const response = await fetch(`/api/podcast/search?${params}`)
    const data = await response.json()

    podcastResults.value = data.podcasts || []
    episodeResults.value = data.episodes || []
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
}

.search-bar .input-text {
  flex: 1;
}

.filters-toggle {
  display: flex;
}

.filters-panel {
  display: flex;
  gap: var(--space-04);
  padding: var(--space-04);
  background: var(--color-background-subtle);
  border-radius: var(--radius-04);
}

.filter-group {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.filter-group label {
  font-size: var(--font-size-sm);
  font-weight: var(--font-weight-medium);
  color: var(--color-text-muted);
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
