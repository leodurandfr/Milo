<template>
  <div class="queue-view">
    <LoadingSpinner v-if="loading" />

    <div v-else-if="episodes.length === 0" class="empty-state">
      <Icon name="list" :size="48" />
      <p>Aucun épisode en cours</p>
      <p class="hint">Commencez à écouter des épisodes et ils apparaîtront ici</p>
    </div>

    <div v-else class="episodes-list">
      <div v-for="episode in episodes" :key="episode.episodeUuid" class="queue-item">
        <EpisodeCard
          :episode="formatQueueEpisode(episode)"
          @select="$emit('select-episode', episode.episodeUuid)"
          @play="$emit('play-episode', formatQueueEpisode(episode))"
        />
        <div class="queue-actions">
          <Button
            variant="secondary"
            size="small"
            @click="markComplete(episode.episodeUuid)"
          >
            <Icon name="check" :size="16" />
            Terminer
          </Button>
          <Button
            variant="secondary"
            size="small"
            @click="removeFromQueue(episode.episodeUuid)"
          >
            <Icon name="close" :size="16" />
            Supprimer
          </Button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import EpisodeCard from './EpisodeCard.vue'
import Button from '@/components/ui/Button.vue'
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue'
import Icon from '@/components/ui/Icon.vue'

const emit = defineEmits(['select-episode', 'play-episode'])

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
  gap: var(--space-03);
}

.queue-item {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.queue-actions {
  display: flex;
  gap: var(--space-02);
  padding-left: 92px; /* Align with card content */
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
