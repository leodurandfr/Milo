  <template>
    <div class="subscriptions-view">
      <!-- My podcasts -->
      <section class="section">
        <!-- <h2 class="section-title heading-2">{{ t('podcasts.myPodcasts') }}</h2> -->

        <MessageContent v-if="loading" loading :title="t('podcasts.loading')" />

        <MessageContent
          v-else-if="subscriptions.length === 0"
          icon="heartOff"
          :title="t('podcasts.noSubscriptions')"
          :subtitle="t('podcasts.noSubscriptionsHint')"
        />

        <div v-else class="podcasts-grid">
          <PodcastCard v-for="sub in subscriptions" :key="sub.uuid" :podcast="formatSubscription(sub)"
            :showActions="true" @select="$emit('select-podcast', sub.uuid)" @unsubscribe="handleUnsubscribe" />
        </div>
      </section>

      <!-- Latest episodes from subscriptions -->
      <section v-if="latestEpisodes.length > 0" class="section">

        <h2 class="section-title heading-2">{{ t('podcasts.newEpisodes') }}</h2>

        <div class="episodes-list">
          <EpisodeCard v-for="episode in latestEpisodes" :key="episode.uuid" :episode="episode"
            @select="$emit('select-episode', episode.uuid)" @play="$emit('play-episode', episode)"
            @pause="handlePause" @select-podcast="(podcast) => $emit('select-podcast', podcast)" />
        </div>
      </section>
    </div>
  </template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { usePodcastStore } from '@/stores/podcastStore'
import { useI18n } from '@/services/i18n'
import PodcastCard from './PodcastCard.vue'
import EpisodeCard from './EpisodeCard.vue'
import MessageContent from '@/components/ui/MessageContent.vue'

const { t } = useI18n()
const emit = defineEmits(['select-podcast', 'select-episode', 'play-episode'])
const podcastStore = usePodcastStore()

async function handlePause() {
  await podcastStore.pause()
}

const loading = ref(false)

// Use store's cached data directly via computed
const subscriptions = computed(() => podcastStore.subscriptions)
const latestEpisodes = computed(() => podcastStore.latestSubscriptionEpisodes)

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
      podcastStore.removeSubscription(uuid)
    }
  } catch (error) {
    console.error('Error unsubscribing:', error)
  }
}

async function loadData() {
  // Only show loading if no cached data available
  if (!podcastStore.subscriptionsLoaded) {
    loading.value = true
  }

  try {
    await podcastStore.loadSubscriptions()
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
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: var(--space-02);
}

@media (max-aspect-ratio: 4/3) {
  .podcasts-grid {
    grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  }
}
</style>