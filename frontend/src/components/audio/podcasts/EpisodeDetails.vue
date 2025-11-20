<template>
  <div class="episode-details">
    <LoadingSpinner v-if="loading" />

    <template v-else-if="episode">
      <div class="details-header">
        <img :src="episode.image_url" :alt="episode.name" class="episode-image" />
        <div class="header-info">
          <h2>{{ episode.name }}</h2>
          <p class="podcast-link" @click="$emit('select-podcast', episode.podcast?.uuid)">
            {{ episode.podcast?.name }}
          </p>
          <p class="meta">
            {{ formattedDuration }} • {{ formattedDate }}
          </p>
          <div class="badges">
            <span v-if="episode.episode_type !== 'FULL'" class="badge">
              {{ episode.episode_type }}
            </span>
            <span v-if="episode.season_number" class="badge">
              S{{ episode.season_number }} E{{ episode.episode_number }}
            </span>
          </div>
          <Button variant="toggle" @click="$emit('play-episode', episode)">
            <SvgIcon name="play" :size="16" />
            {{ hasProgress ? 'Reprendre' : 'Écouter' }}
          </Button>
        </div>
      </div>

      <div class="description">
        <h3>Description</h3>
        <p>{{ episode.description }}</p>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import Button from '@/components/ui/Button.vue'
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue'
import SvgIcon from '@/components/ui/SvgIcon.vue'

const props = defineProps({
  uuid: {
    type: String,
    required: true
  }
})

defineEmits(['back', 'play-episode', 'select-podcast'])

const loading = ref(false)
const episode = ref(null)

const hasProgress = computed(() => {
  return episode.value?.playback_progress?.position > 0
})

const formattedDuration = computed(() => {
  const seconds = episode.value?.duration || 0
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  if (h > 0) return `${h}h ${m}min`
  return `${m} min`
})

const formattedDate = computed(() => {
  if (!episode.value?.date_published) return ''
  const date = new Date(episode.value.date_published * 1000)
  return date.toLocaleDateString('fr-FR', {
    day: 'numeric',
    month: 'long',
    year: 'numeric'
  })
})

async function loadEpisode() {
  loading.value = true
  try {
    const response = await fetch(`/api/podcast/episode/${props.uuid}`)
    episode.value = await response.json()
  } catch (error) {
    console.error('Error loading episode:', error)
  } finally {
    loading.value = false
  }
}

watch(() => props.uuid, loadEpisode, { immediate: false })
onMounted(loadEpisode)
</script>

<style scoped>
.episode-details {
  display: flex;
  flex-direction: column;
  gap: var(--space-05);
}

.details-header {
  display: flex;
  gap: var(--space-04);
}

.episode-image {
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
}

.podcast-link {
  font-size: var(--font-size-md);
  color: var(--color-accent);
  cursor: pointer;
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
}

.description h3 {
  font-size: var(--font-size-lg);
  font-weight: var(--font-weight-semibold);
  margin: 0 0 var(--space-03);
}

.description p {
  font-size: var(--font-size-sm);
  line-height: 1.6;
  margin: 0;
}
</style>
