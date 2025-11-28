<template>
  <div class="genre-view">
    <!-- Top podcasts of the genre -->
    <section class="section">
      <div v-if="loading" class="message-wrapper">
        <MessageContent loading>
          <p class="heading-2">{{ t('podcasts.loading') }}</p>
        </MessageContent>
      </div>
      <div v-else-if="topPodcasts.length === 0" class="message-wrapper">
        <MessageContent>
          <SvgIcon name="podcast" :size="64" color="var(--color-background-medium-16)" />
          <p class="heading-2">{{ t('podcasts.noPodcastsInGenre') }}</p>
        </MessageContent>
      </div>
      <div v-else class="podcasts-grid">
        <PodcastCard v-for="podcast in topPodcasts" :key="podcast.itunes_id || podcast.uuid" :podcast="podcast"
          :isLoading="isPodcastLoading(podcast)" @select="$emit('select-podcast', podcast)" />
      </div>
    </section>
  </div>
</template>

<script setup>
import { ref, onMounted, watch } from 'vue'
import { usePodcastStore } from '@/stores/podcastStore'
import { useI18n } from '@/services/i18n'
import PodcastCard from './PodcastCard.vue'
import SvgIcon from '@/components/ui/SvgIcon.vue'
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

const podcastStore = usePodcastStore()

const loading = ref(false)

// Check if a specific podcast is currently loading (lookup in progress)
function isPodcastLoading(podcast) {
  if (!props.loadingPodcastId) return false
  return podcast.itunes_id === props.loadingPodcastId || podcast.uuid === props.loadingPodcastId
}
const topPodcasts = ref([])

async function loadData() {
  // Fetch top podcasts for this genre
  // Language is automatically retrieved from /var/lib/milo/settings.json
  // Default limit is 30 podcasts (configurable up to 200)
  loading.value = true

  try {
    const response = await fetch(
      `/api/podcast/discover/by-genre?genre=${props.genre}&limit=30`
    )

    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`)
    }

    const data = await response.json()

    // Update podcasts (from iTunes RSS with Taddy UUIDs)
    topPodcasts.value = data.podcasts || []

    console.log(`Loaded ${topPodcasts.value.length} podcasts for genre ${props.genre} in ${data.language}/${data.country}`)
  } catch (error) {
    console.error('Error loading genre content:', error)
    topPodcasts.value = []
  } finally {
    loading.value = false
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
  grid-template-columns: repeat(4, 1fr);
  gap: var(--space-02);
}

.message-wrapper {
  background: var(--color-background-neutral);
  border-radius: var(--radius-04);
}

@media (max-aspect-ratio: 4/3) {
  .podcasts-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}

</style>
