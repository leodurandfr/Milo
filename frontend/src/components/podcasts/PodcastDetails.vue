<template>
  <div class="podcast-details">
    <div class="transition-container">
      <!-- Skeleton state -->
      <transition name="content-fade">
        <SkeletonPodcastDetails v-if="loading" key="loading" />
      </transition>

      <!-- Real content -->
      <transition name="content-fade">
        <div v-if="!loading && podcast" key="loaded" class="details-content">
          <!-- Podcast Card (row variant) -->
          <PodcastCard
            :podcast="podcast"
            variant="row"
            :clickable="false"
            contrast
            @subscribe="handleSubscribe"
            @unsubscribe="handleUnsubscribe"
          />

          <!-- Episodes list -->
          <div class="episodes-section">
            <h3 class="heading-2">{{ t('podcasts.episodesTitle') }}</h3>
            <div class="episodes-list">
              <EpisodeCard
                v-for="episode in allEpisodes"
                :key="episode.uuid"
                :episode="episode"
                @select="$emit('select-episode', episode.uuid)"
                @play="$emit('play-episode', episode)"
                @pause="handlePause"
              />
            </div>

            <!-- Load more button -->
            <div v-if="hasMoreEpisodes" class="load-more-container">
              <Button
                variant="brand"
                :loading="loadingMore"
                @click="loadMoreEpisodes"
              >
                {{ t('podcasts.loadMoreEpisodes') }}
              </Button>
            </div>
          </div>
        </div>
      </transition>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { usePodcastStore } from '@/stores/podcastStore'
import { useI18n } from '@/services/i18n'
import PodcastCard from './PodcastCard.vue'
import EpisodeCard from './EpisodeCard.vue'
import Button from '@/components/ui/Button.vue'
import SkeletonPodcastDetails from './SkeletonPodcastDetails.vue'

const { t } = useI18n()

const props = defineProps({
  uuid: {
    type: String,
    required: true
  }
})

const emit = defineEmits(['back', 'play-episode', 'select-episode'])
const podcastStore = usePodcastStore()

async function handlePause() {
  await podcastStore.pause()
}

const loading = ref(false)
const podcast = ref(null)
const currentPage = ref(1)
const loadingMore = ref(false)
const allEpisodes = ref([])

// Computed to check if more episodes available
const hasMoreEpisodes = computed(() => {
  if (!podcast.value) return false
  return allEpisodes.value.length < podcast.value.total_episodes
})

async function loadPodcast() {
  loading.value = true
  currentPage.value = 1
  try {
    const response = await fetch(`/api/podcast/series/${props.uuid}?page=1&limit=25`)
    podcast.value = await response.json()

    // Initialize episodes array
    allEpisodes.value = podcast.value.episodes || []

    // Enrich episodes with progress cache
    if (allEpisodes.value.length > 0) {
      podcastStore.enrichEpisodesWithProgress(allEpisodes.value)
    }
  } catch (error) {
    console.error('Error loading podcast:', error)
  } finally {
    loading.value = false
  }
}

async function handleSubscribe() {
  if (!podcast.value) return

  try {
    const response = await fetch('/api/podcast/subscriptions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        uuid: props.uuid,
        name: podcast.value.name || '',
        image_url: podcast.value.image_url || '',
        children_hash: podcast.value.children_hash || ''
      })
    })
    if (response.ok) {
      podcast.value.is_subscribed = true
    } else {
      console.error('Failed to subscribe:', await response.text())
    }
  } catch (error) {
    console.error('Error subscribing:', error)
  }
}

async function handleUnsubscribe() {
  if (!podcast.value) return

  try {
    const response = await fetch(`/api/podcast/subscriptions/${props.uuid}`, { method: 'DELETE' })
    if (response.ok) {
      podcast.value.is_subscribed = false
    } else {
      console.error('Failed to unsubscribe:', await response.text())
    }
  } catch (error) {
    console.error('Error unsubscribing:', error)
  }
}

async function loadMoreEpisodes() {
  if (loadingMore.value || !hasMoreEpisodes.value) return

  loadingMore.value = true
  currentPage.value++

  try {
    const response = await fetch(
      `/api/podcast/series/${props.uuid}?page=${currentPage.value}&limit=25`
    )
    const data = await response.json()

    const newEpisodes = data.episodes || []

    // Enrich with progress
    podcastStore.enrichEpisodesWithProgress(newEpisodes)

    // Append to existing episodes
    allEpisodes.value = [...allEpisodes.value, ...newEpisodes]
  } catch (error) {
    console.error('Error loading more episodes:', error)
    currentPage.value-- // Rollback on error
  } finally {
    loadingMore.value = false
  }
}

watch(() => props.uuid, async () => {
  await loadPodcast()
}, { immediate: false })

onMounted(() => {
  loadPodcast()
})
</script>

<style scoped>
.podcast-details {
  display: flex;
  flex-direction: column;
}

.transition-container {
  display: grid;
  grid-template-columns: 1fr;
}

.transition-container > * {
  grid-column: 1;
  grid-row: 1;
}

.content-fade-enter-active,
.content-fade-leave-active {
  transition: opacity var(--transition-normal);
}

.content-fade-enter-from,
.content-fade-leave-to {
  opacity: 0;
}

.details-content {
  display: flex;
  flex-direction: column;
  gap: var(--space-06);
  min-width: 0;
}

.episodes-section h3 {
  margin: 0 0 var(--space-03);
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
</style>
