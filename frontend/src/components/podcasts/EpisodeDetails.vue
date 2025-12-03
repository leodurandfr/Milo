<template>
  <div class="episode-details">
    <div class="transition-container">
      <!-- Skeleton state -->
      <transition name="content-fade">
        <SkeletonEpisodeDetails v-if="loading" key="loading" />
      </transition>

      <!-- Real content -->
      <transition name="content-fade">
        <div v-if="!loading && episode" key="loaded" class="details-content">
          <!-- Episode Card -->
          <EpisodeCard
            :episode="enrichedEpisode"
            contrast
            @select="handlePlayClick"
            @play="handlePlayClick"
            @pause="handlePause"
            @select-podcast="handleSelectPodcast"
          />

          <!-- Description block -->
          <div class="description-block">
            <h3 class="text-mono description-title">{{ t('podcasts.description') }}</h3>
            <p class="text-body">{{ episode.description }}</p>
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
import EpisodeCard from './EpisodeCard.vue'
import SkeletonEpisodeDetails from './SkeletonEpisodeDetails.vue'

const { t } = useI18n()

const props = defineProps({
  uuid: {
    type: String,
    required: true
  }
})

const emit = defineEmits(['back', 'play-episode', 'select-podcast'])

const podcastStore = usePodcastStore()
const loading = ref(false)
const episode = ref(null)

// Enriched episode with podcast info for EpisodeCard
const enrichedEpisode = computed(() => {
  if (!episode.value) return null
  return {
    ...episode.value,
    podcast: episode.value.podcast || null
  }
})

async function loadEpisode() {
  loading.value = true
  try {
    const response = await fetch(`/api/podcast/episode/${props.uuid}`)
    episode.value = await response.json()

    // Enrich with progress cache if available
    if (episode.value && episode.value.playback_progress) {
      podcastStore.enrichEpisodesWithProgress([episode.value])
    }
  } catch (error) {
    console.error('Error loading episode:', error)
  } finally {
    loading.value = false
  }
}

function handlePlayClick() {
  emit('play-episode', episode.value)
}

async function handlePause() {
  await podcastStore.pause()
}

function handleSelectPodcast(podcast) {
  emit('select-podcast', podcast)
}

watch(() => props.uuid, async () => {
  await loadEpisode()
}, { immediate: false })

onMounted(() => {
  loadEpisode()
})
</script>

<style scoped>
.episode-details {
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
  gap: var(--space-03);
  min-width: 0;
}

.description-block {
  background: var(--color-background-neutral);
  border-radius: var(--radius-04);
  padding: var(--space-04);
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
  min-width: 0;
  box-sizing: border-box;
}

.description-title {
  color: var(--color-text-secondary);
  margin: 0;
}

.description-block p {
  margin: 0;
}
</style>
