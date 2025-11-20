<template>
  <div class="subscriptions-view">
    <!-- Latest episodes from subscriptions -->
    <section v-if="latestEpisodes.length > 0" class="section">
      <h3 class="section-title">Nouveaux épisodes</h3>
      <div class="episodes-list">
        <EpisodeCard
          v-for="episode in latestEpisodes"
          :key="episode.uuid"
          :episode="episode"
          @select="$emit('select-episode', episode.uuid)"
          @play="$emit('play-episode', episode)"
          @pause="handlePause"
        />
      </div>
    </section>

    <!-- My podcasts -->
    <section class="section">
      <h3 class="section-title">Mes podcasts</h3>

      <LoadingSpinner v-if="loading" />

      <div v-else-if="subscriptions.length === 0" class="empty-state">
        <SvgIcon name="heart" :size="48" />
        <p>Aucun abonnement</p>
        <p class="hint">Recherchez des podcasts et abonnez-vous pour les retrouver ici</p>
      </div>

      <div v-else class="podcasts-grid">
        <PodcastCard
          v-for="sub in subscriptions"
          :key="sub.uuid"
          :podcast="formatSubscription(sub)"
          :showActions="true"
          @select="$emit('select-podcast', sub.uuid)"
          @unsubscribe="handleUnsubscribe"
        />
      </div>
    </section>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { usePodcastStore } from '@/stores/podcastStore'
import PodcastCard from './PodcastCard.vue'
import EpisodeCard from './EpisodeCard.vue'
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue'
import SvgIcon from '@/components/ui/SvgIcon.vue'

const emit = defineEmits(['select-podcast', 'select-episode', 'play-episode'])
const podcastStore = usePodcastStore()

async function handlePause() {
  await podcastStore.pause()
}

const loading = ref(false)
const subscriptions = ref([])
const latestEpisodes = ref([])

function formatSubscription(sub) {
  return {
    uuid: sub.uuid,
    name: sub.name,
    image_url: sub.imageUrl,
    total_episodes: 0,
    is_subscribed: true,
    publisher: ''
  }
}

async function handleUnsubscribe(uuid) {
  try {
    const response = await fetch(`/api/podcast/subscriptions/${uuid}`, {
      method: 'DELETE'
    })
    if (response.ok) {
      subscriptions.value = subscriptions.value.filter(s => s.uuid !== uuid)
    }
  } catch (error) {
    console.error('Error unsubscribing:', error)
  }
}

async function loadData() {
  loading.value = true

  try {
    // Load subscriptions
    const subResponse = await fetch('/api/podcast/subscriptions')
    const subData = await subResponse.json()
    subscriptions.value = subData.subscriptions || []

    // Load latest episodes if there are subscriptions
    if (subscriptions.value.length > 0) {
      const latestResponse = await fetch('/api/podcast/subscriptions/latest-episodes?limit=20')
      const latestData = await latestResponse.json()
      latestEpisodes.value = latestData.results || []
    }
  } catch (error) {
    console.error('Error loading subscriptions:', error)
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  loadData()
})
</script>

<style scoped>
.subscriptions-view {
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

.episodes-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.podcasts-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: var(--space-03);
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

.empty-state .hint {
  font-size: var(--font-size-sm);
  opacity: 0.7;
}
</style>
