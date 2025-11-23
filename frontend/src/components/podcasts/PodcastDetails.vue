<template>
  <div class="podcast-details">
    <LoadingSpinner v-if="loading" />

    <template v-else-if="podcast">
      <!-- Header -->
      <div class="details-header">
        <img :src="podcast.image_url" :alt="podcast.name" class="podcast-image" />
        <div class="header-info">
          <h2 class="heading-1">{{ podcast.name }}</h2>
          <p class="publisher text-body">{{ podcast.publisher || podcast.author }}</p>
          <p class="meta text-mono">
            {{ podcast.total_episodes }} {{ t('podcasts.episodesCount2') }}
          </p>
          <div v-if="podcast.is_completed || podcast.is_explicit" class="badges">
            <span v-if="podcast.is_completed" class="badge text-mono">{{ t('podcasts.completed') }}</span>
            <span v-if="podcast.is_explicit" class="badge warning text-mono">{{ t('podcasts.explicit') }}</span>
          </div>
          <Button
            :variant="podcast.is_subscribed ? 'toggle' : 'ghost'"
            @click="toggleSubscription"
          >
            {{ podcast.is_subscribed ? t('podcasts.subscribed') : t('podcasts.subscribe') }}
          </Button>
        </div>
      </div>

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
            variant="primary"
            :loading="loadingMore"
            @click="loadMoreEpisodes"
          >
            {{ t('podcasts.loadMoreEpisodes') }}
          </Button>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { usePodcastStore } from '@/stores/podcastStore'
import { useI18n } from '@/services/i18n'
import EpisodeCard from './EpisodeCard.vue'
import Button from '@/components/ui/Button.vue'
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue'

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

async function toggleSubscription() {
  if (!podcast.value) return

  try {
    if (podcast.value.is_subscribed) {
      const response = await fetch(`/api/podcast/subscriptions/${props.uuid}`, { method: 'DELETE' })
      if (response.ok) {
        podcast.value.is_subscribed = false
      } else {
        console.error('Failed to unsubscribe:', await response.text())
      }
    } else {
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
    }
  } catch (error) {
    console.error('Error toggling subscription:', error)
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

watch(() => props.uuid, loadPodcast, { immediate: false })

onMounted(() => {
  loadPodcast()
})
</script>

<style scoped>
.podcast-details {
  display: flex;
  flex-direction: column;
  gap: var(--space-05);
}

.details-header {
  display: flex;
  gap: var(--space-04);
}

.podcast-image {
  width: 150px;
  height: 150px;
  border-radius: var(--radius-04);
  object-fit: cover;
  flex-shrink: 0;
}

.header-info {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.header-info h2 {
  margin: 0;
}

.publisher {
  color: var(--color-text-secondary);
  margin: 0;
}

.meta {
  color: var(--color-text-secondary);
  margin: 0;
}

.badges {
  display: flex;
  gap: var(--space-02);
}

.badge {
  padding: var(--space-01) var(--space-02);
  background: var(--color-background-neutral);
  border-radius: var(--radius-02);
  color: var(--color-text-secondary);
}

.badge.warning {
  background: var(--color-warning);
  color: white;
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
