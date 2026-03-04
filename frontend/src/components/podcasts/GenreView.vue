<template>
  <div class="genre-view">
    <!-- Top podcasts of the genre -->
    <section class="section">
      <MessageContent v-if="loading" loading :title="t('podcasts.loading')" />
      <MessageContent v-else-if="topPodcasts.length === 0" icon="podcast" :title="t('podcasts.noPodcastsInGenre')" />
      <div v-else class="podcasts-grid">
        <PodcastCard v-for="podcast in topPodcasts" :key="podcast.itunes_id || podcast.uuid" :podcast="podcast"
          :isLoading="isPodcastLoading(podcast)" @select="$emit('select-podcast', podcast)" />
      </div>
    </section>
  </div>
</template>

<script setup>
import { ref, onMounted, watch } from 'vue'
import { useI18n } from '@/services/i18n'
import { logger } from '@/services/logger'
import axios from 'axios'
import { useAsyncData } from '@/composables/useAsyncData'
import PodcastCard from './PodcastCard.vue'
import MessageContent from '@/components/ui/MessageContent.vue'

const { t } = useI18n()

const props = defineProps({
  genre: {
    type: String,
    required: true
  },
  genreLabel: {
    type: String,
    required: true
  },
  loadingPodcastId: {
    type: [String, Number],
    default: null
  }
})

const emit = defineEmits(['select-podcast'])

// Check if a specific podcast is currently loading (lookup in progress)
function isPodcastLoading(podcast) {
  if (!props.loadingPodcastId) return false
  return podcast.itunes_id === props.loadingPodcastId || podcast.uuid === props.loadingPodcastId
}
const topPodcasts = ref([])

const { loading, execute: loadData } = useAsyncData(async () => {
  const { data } = await axios.get('/api/podcast/discover/by-genre', {
    params: { genre: props.genre, limit: 30 }
  })
  topPodcasts.value = data.podcasts || []
  logger.debug('podcast', `Loaded ${topPodcasts.value.length} podcasts for genre ${props.genre} in ${data.language}/${data.country}`)
}, { logTag: 'podcast' })

watch(() => props.genre, loadData)
onMounted(loadData)
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

.podcasts-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: var(--space-02);
}

@media (max-aspect-ratio: 4/3) {
  .podcasts-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}

</style>
