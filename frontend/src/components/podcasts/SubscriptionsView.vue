<template>
  <div class="subscriptions-view">
    <!-- My podcasts -->
    <section class="section">
      <h2 class="section-title heading-2">{{ t('podcasts.myPodcasts') }}</h2>

      <div v-if="loading" class="message-wrapper">
        <MessageContent loading>
          <p class="heading-2">{{ t('podcasts.loading') }}</p>
        </MessageContent>
      </div>

      <div v-else-if="subscriptions.length === 0" class="message-wrapper">
        <MessageContent>
          <SvgIcon name="heartOff" :size="64" color="var(--color-background-medium-16)" />
          <p class="heading-2">{{ t('podcasts.noSubscriptions') }}</p>
          <p class="text-mono">{{ t('podcasts.noSubscriptionsHint') }}</p>
        </MessageContent>
      </div>

      <div v-else class="podcasts-grid">
        <PodcastCard v-for="sub in subscriptions" :key="sub.uuid" :podcast="formatSubscription(sub)" :showActions="true"
          @select="$emit('select-podcast', sub.uuid)" @unsubscribe="handleUnsubscribe" />
      </div>
    </section>

    <!-- Latest episodes from subscriptions -->
    <section v-if="latestEpisodes.length > 0" class="section">

      <h2 class="section-title heading-2">{{ t('podcasts.newEpisodes') }}</h2>

      <div class="episodes-list">
        <EpisodeCard v-for="episode in latestEpisodes" :key="episode.uuid" :episode="episode"
          @select="$emit('select-episode', episode.uuid)" @play="$emit('play-episode', episode)" @pause="handlePause" />
      </div>
    </section>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { usePodcastStore } from '@/stores/podcastStore'
import { useI18n } from '@/services/i18n'
import PodcastCard from './PodcastCard.vue'
import EpisodeCard from './EpisodeCard.vue'
import SvgIcon from '@/components/ui/SvgIcon.vue'
import MessageContent from '@/components/ui/MessageContent.vue'

const { t } = useI18n()
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
      latestEpisodes.value = podcastStore.enrichEpisodesWithProgress(latestData.results || [])
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


.episodes-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.podcasts-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: var(--space-02);
}

.message-wrapper {
  background: var(--color-background-neutral);
  border-radius: var(--radius-04);
}
</style>
