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
      <div v-for="episode in episodes" :key="episode.episode_uuid" class="queue-item">
        <EpisodeCard
          :episode="formatQueueEpisode(episode)"
          :show-complete-button="true"
          @select="$emit('select-episode', episode.episode_uuid)"
          @play="$emit('play-episode', formatQueueEpisode(episode))"
          @complete="markComplete(episode.episode_uuid)"
          @select-podcast="(podcast) => $emit('select-podcast', podcast)"
        />
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { usePodcastStore } from '@/stores/podcastStore'
import { useI18n } from '@/services/i18n'
import { apiCall } from '@/services/apiCall'
import { useAsyncData } from '@/composables/useAsyncData'
import EpisodeCard from './EpisodeCard.vue'
import MessageContent from '@/components/ui/MessageContent.vue'

const { t } = useI18n()
const emit = defineEmits(['select-episode', 'play-episode', 'select-podcast'])
const podcastStore = usePodcastStore()

const episodes = ref([])

function formatQueueEpisode(queueItem) {
  return {
    uuid: queueItem.episode_uuid,
    name: queueItem.episode_name,
    image_url: queueItem.image_url,
    duration: queueItem.duration,
    podcast: {
      uuid: queueItem.podcast_uuid,
      name: queueItem.podcast_name
    },
    playback_progress: {
      position: queueItem.position,
      duration: queueItem.duration,
      completed: queueItem.completed
    }
  }
}

const { loading, execute: loadQueue } = useAsyncData(async () => {
  const result = await apiCall.get('/api/podcast/queue', {
    category: 'podcast',
    message: 'Error loading queue',
  })
  if (result.ok) {
    episodes.value = result.data.episodes || []
    podcastStore.enrichEpisodesWithProgress(episodes.value.map(formatQueueEpisode))
  }
})

async function markComplete(episodeUuid) {
  const result = await apiCall.post(`/api/podcast/queue/${episodeUuid}/complete`, null, {
    category: 'podcast',
    message: 'Error marking episode complete',
  })
  if (result.ok) {
    episodes.value = episodes.value.filter(e => e.episode_uuid !== episodeUuid)
  }
}

onMounted(loadQueue)
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
