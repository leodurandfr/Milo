<template>
  <div class="podcast-details">
    <LoadingSpinner v-if="loading" />

    <template v-else-if="podcast">
      <!-- Header -->
      <div class="details-header">
        <img :src="podcast.image_url" :alt="podcast.name" class="podcast-image" />
        <div class="header-info">
          <h2>{{ podcast.name }}</h2>
          <p class="publisher">{{ podcast.publisher || podcast.author }}</p>
          <p class="meta">
            {{ podcast.total_episodes }} {{ t('podcasts.episodesCount2') }} • {{ podcast.language }}
          </p>
          <div class="badges">
            <span v-if="podcast.is_completed" class="badge">{{ t('podcasts.completed') }}</span>
            <span v-if="podcast.is_explicit" class="badge warning">{{ t('podcasts.explicit') }}</span>
          </div>
          <Button
            :variant="podcast.is_subscribed ? 'toggle' : 'ghost'"
            @click="toggleSubscription"
          >
            {{ podcast.is_subscribed ? t('podcasts.subscribed') : t('podcasts.subscribe') }}
          </Button>
        </div>
      </div>

      <!-- Description -->
      <div class="description">
        <h3>{{ t('podcasts.description') }}</h3>
        <p>{{ podcast.description }}</p>
      </div>

      <!-- Episodes list -->
      <div class="episodes-section">
        <h3>{{ t('podcasts.episodesTitle') }}</h3>
        <div class="episodes-list">
          <EpisodeCard
            v-for="episode in podcast.episodes"
            :key="episode.uuid"
            :episode="episode"
            @select="$emit('select-episode', episode.uuid)"
            @play="$emit('play-episode', episode)"
            @pause="handlePause"
          />
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, onMounted, watch } from 'vue'
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

async function loadPodcast() {
  loading.value = true
  try {
    const response = await fetch(`/api/podcast/series/${props.uuid}`)
    podcast.value = await response.json()

    // Enrich episodes with progress cache
    if (podcast.value && podcast.value.episodes) {
      podcastStore.enrichEpisodesWithProgress(podcast.value.episodes)
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
  font-size: var(--font-size-xl);
  font-weight: var(--font-weight-bold);
  margin: 0;
  color: var(--color-text);
}

.publisher {
  font-size: var(--font-size-md);
  color: var(--color-text-muted);
  margin: 0;
}

.meta {
  font-size: var(--font-size-sm);
  color: var(--color-text-muted);
  margin: 0;
}

.badges {
  display: flex;
  gap: var(--space-02);
}

.badge {
  font-size: var(--font-size-xs);
  padding: var(--space-01) var(--space-02);
  background: var(--color-background-neutral);
  border-radius: var(--radius-02);
  color: var(--color-text-muted);
}

.badge.warning {
  background: var(--color-warning);
  color: white;
}

.description h3,
.episodes-section h3 {
  font-size: var(--font-size-lg);
  font-weight: var(--font-weight-semibold);
  margin: 0 0 var(--space-03);
  color: var(--color-text);
}

.description p {
  font-size: var(--font-size-sm);
  line-height: 1.6;
  color: var(--color-text);
  margin: 0;
}

.episodes-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}
</style>
