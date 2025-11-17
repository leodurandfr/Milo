<template>
  <div class="genre-view">
    <!-- Top podcasts of the genre -->
    <section class="section">
      <h3 class="section-title">Top Podcasts {{ genreLabel }}</h3>
      <LoadingSpinner v-if="loadingPodcasts" />
      <div v-else-if="topPodcasts.length === 0" class="empty-state">
        <p>Aucun podcast trouvé dans cette catégorie</p>
      </div>
      <div v-else class="podcasts-grid">
        <PodcastCard
          v-for="podcast in topPodcasts.slice(0, 6)"
          :key="podcast.uuid"
          :podcast="podcast"
          @select="$emit('select-podcast', podcast.uuid)"
        />
      </div>
    </section>

    <!-- Top episodes of the genre -->
    <section class="section">
      <h3 class="section-title">Top Épisodes {{ genreLabel }}</h3>
      <LoadingSpinner v-if="loadingEpisodes" />
      <div v-else-if="topEpisodes.length === 0" class="empty-state">
        <p>Aucun épisode trouvé dans cette catégorie</p>
      </div>
      <div v-else class="episodes-list">
        <EpisodeCard
          v-for="episode in topEpisodes.slice(0, 6)"
          :key="episode.uuid"
          :episode="episode"
          @select="$emit('select-episode', episode.uuid)"
          @play="$emit('play-episode', episode)"
        />
      </div>
    </section>
  </div>
</template>

<script setup>
import { ref, onMounted, watch } from 'vue'
import { usePodcastStore } from '@/stores/podcastStore'
import PodcastCard from './PodcastCard.vue'
import EpisodeCard from './EpisodeCard.vue'
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue'

const props = defineProps({
  genre: {
    type: String,
    required: true
  },
  genreLabel: {
    type: String,
    required: true
  }
})

const emit = defineEmits(['select-podcast', 'select-episode', 'play-episode'])

const podcastStore = usePodcastStore()

const loadingPodcasts = ref(false)
const loadingEpisodes = ref(false)
const topPodcasts = ref([])
const topEpisodes = ref([])

async function loadData() {
  const settings = podcastStore.settings
  const country = settings.defaultCountry || 'FRANCE'
  const language = settings.defaultLanguage || 'FRENCH'

  // Load top podcasts of this genre (try with language first, then without)
  loadingPodcasts.value = true
  try {
    // First try with language filter
    let podcastsResponse = await fetch(
      `/api/podcast/discover/popular?language=${language}&genres=${props.genre}&limit=10`
    )
    let podcastsData = await podcastsResponse.json()
    topPodcasts.value = podcastsData.results || []

    // If no results, try with country filter
    if (topPodcasts.value.length === 0) {
      podcastsResponse = await fetch(
        `/api/podcast/discover/genres?genres=${props.genre}&country=${country}&content_type=PODCASTSERIES&limit=10`
      )
      podcastsData = await podcastsResponse.json()
      topPodcasts.value = podcastsData.results || []
    }

    // If still no results, try without any filter
    if (topPodcasts.value.length === 0) {
      podcastsResponse = await fetch(
        `/api/podcast/discover/genres?genres=${props.genre}&content_type=PODCASTSERIES&limit=10`
      )
      podcastsData = await podcastsResponse.json()
      topPodcasts.value = podcastsData.results || []
    }
  } catch (error) {
    console.error('Error loading genre podcasts:', error)
  } finally {
    loadingPodcasts.value = false
  }

  // Load top episodes of this genre (try with country first, then without)
  loadingEpisodes.value = true
  try {
    // First try with country filter
    let episodesResponse = await fetch(
      `/api/podcast/discover/genres?genres=${props.genre}&country=${country}&content_type=PODCASTEPISODE&limit=10`
    )
    let episodesData = await episodesResponse.json()
    topEpisodes.value = episodesData.results || []

    // If no results, try without country filter
    if (topEpisodes.value.length === 0) {
      episodesResponse = await fetch(
        `/api/podcast/discover/genres?genres=${props.genre}&content_type=PODCASTEPISODE&limit=10`
      )
      episodesData = await episodesResponse.json()
      topEpisodes.value = episodesData.results || []
    }
  } catch (error) {
    console.error('Error loading genre episodes:', error)
  } finally {
    loadingEpisodes.value = false
  }
}

watch(() => props.genre, loadData)

onMounted(() => {
  loadData()
})
</script>

<style scoped>
.genre-view {
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
  padding: var(--space-06);
  text-align: center;
  color: var(--color-text-muted);
}

.empty-state p {
  margin: 0;
  font-size: var(--font-size-sm);
}
</style>
