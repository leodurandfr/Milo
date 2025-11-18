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
          v-for="podcast in topPodcasts"
          :key="podcast.uuid"
          :podcast="podcast"
          @select="$emit('select-podcast', podcast.uuid)"
        />
      </div>
    </section>

    <!-- Top episodes of the genre (hidden if no results) -->
    <section v-if="!loadingEpisodes && topEpisodes.length > 0" class="section">
      <h3 class="section-title">Top Épisodes {{ genreLabel }}</h3>
      <div class="episodes-list">
        <EpisodeCard
          v-for="episode in topEpisodes"
          :key="episode.uuid"
          :episode="episode"
          @select="$emit('select-episode', episode.uuid)"
          @play="$emit('play-episode', episode)"
        />
      </div>
    </section>
    <section v-else-if="loadingEpisodes" class="section">
      <h3 class="section-title">Top Épisodes {{ genreLabel }}</h3>
      <LoadingSpinner />
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
  // Use a single unified endpoint that fetches both podcasts and episodes
  // Language is automatically retrieved from /var/lib/milo/settings.json
  loadingPodcasts.value = true
  loadingEpisodes.value = true

  try {
    const response = await fetch(
      `/api/podcast/discover/by-genre?genre=${props.genre}&podcasts_limit=5&episodes_limit=5`
    )

    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`)
    }

    const data = await response.json()

    // Update podcasts (always from Taddy with UUIDs)
    topPodcasts.value = data.podcasts || []

    // Update episodes (may be empty array if none found for this genre/language)
    topEpisodes.value = data.episodes || []

    console.log(`Loaded ${topPodcasts.value.length} podcasts and ${topEpisodes.value.length} episodes for genre ${props.genre} in language ${data.language}`)
  } catch (error) {
    console.error('Error loading genre content:', error)
    topPodcasts.value = []
    topEpisodes.value = []
  } finally {
    loadingPodcasts.value = false
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
