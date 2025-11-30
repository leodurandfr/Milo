<template>
  <div class="queue-view">
    <MessageContent v-if="loading" loading :title="t('podcasts.loading')" />

    <MessageContent
      v-else-if="episodes.length === 0"
      icon="podcast"
      :title="t('podcasts.noEpisodesInQueue')"
      :subtitle="t('podcasts.noEpisodesInQueueHint')"
    />

    <div v-else class="episodes-list">
      <div v-for="episode in episodes" :key="episode.episodeUuid" class="queue-item">
        <EpisodeCard
          :episode="formatQueueEpisode(episode)"
          :show-complete-button="true"
          @select="$emit('select-episode', episode.episodeUuid)"
          @play="$emit('play-episode', formatQueueEpisode(episode))"
          @pause="handlePause"
          @complete="markComplete(episode.episodeUuid)"
        />
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { usePodcastStore } from '@/stores/podcastStore'
import { useI18n } from '@/services/i18n'
import EpisodeCard from './EpisodeCard.vue'
import MessageContent from '@/components/ui/MessageContent.vue'

const { t } = useI18n()
const emit = defineEmits(['select-episode', 'play-episode'])
const podcastStore = usePodcastStore()

async function handlePause() {
  await podcastStore.pause()
}

const loading = ref(false)
const episodes = ref([])

function formatQueueEpisode(queueItem) {
  return {
    uuid: queueItem.episodeUuid,
    name: queueItem.episodeName,
    image_url: queueItem.imageUrl,
    duration: queueItem.duration,
    podcast: {
      uuid: queueItem.podcastUuid,
      name: queueItem.podcastName
    },
    playback_progress: {
      position: queueItem.position,
      duration: queueItem.duration,
      completed: queueItem.completed
    }
  }
}

async function loadQueue() {
  loading.value = true
  try {
    const response = await fetch('/api/podcast/queue')
    const data = await response.json()
    episodes.value = data.episodes || []
  } catch (error) {
    console.error('Error loading queue:', error)
  } finally {
    loading.value = false
  }
}

async function markComplete(episodeUuid) {
  try {
    await fetch(`/api/podcast/queue/${episodeUuid}/complete`, { method: 'POST' })
    episodes.value = episodes.value.filter(e => e.episodeUuid !== episodeUuid)
  } catch (error) {
    console.error('Error marking complete:', error)
  }
}

async function removeFromQueue(episodeUuid) {
  try {
    await fetch(`/api/podcast/queue/${episodeUuid}`, { method: 'DELETE' })
    episodes.value = episodes.value.filter(e => e.episodeUuid !== episodeUuid)
  } catch (error) {
    console.error('Error removing from queue:', error)
  }
}

onMounted(() => {
  loadQueue()
})
</script>

<style scoped>
.queue-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

.episodes-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.queue-item {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}
</style>
