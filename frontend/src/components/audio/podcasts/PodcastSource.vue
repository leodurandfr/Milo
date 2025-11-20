<template>
  <div class="podcast-source-wrapper">
    <div class="podcast-container" :class="{ 'with-player': shouldShowPlayerLayout }">
      <!-- Header with navigation -->
      <ModalHeader :title="currentTitle" :subtitle="currentSubtitle" :showBack="currentView !== 'home'" icon="podcast"
        variant="background-neutral" @back="goToHome">
        <template #actions="{ iconType }">
          <IconButton v-if="currentView === 'home'" icon="heart" :type="iconType" :active="false"
            @click="goToSubscriptions" />
          <IconButton v-if="currentView === 'home'" icon="search" :type="iconType" @click="goToSearch" />
          <IconButton v-if="currentView === 'home'" icon="list" :type="iconType" @click="goToQueue" />
        </template>
      </ModalHeader>

      <!-- Main content area -->
      <!-- Home View (Discovery) -->
      <HomeView v-if="currentView === 'home'" @select-podcast="openPodcastDetails" @select-episode="openEpisodeDetails"
        @play-episode="playEpisode" @browse-genre="goToGenre" />

      <!-- Subscriptions View -->
      <SubscriptionsView v-else-if="currentView === 'subscriptions'" @select-podcast="openPodcastDetails"
        @select-episode="openEpisodeDetails" @play-episode="playEpisode" />

      <!-- Search View -->
      <SearchView v-else-if="currentView === 'search'" @select-podcast="openPodcastDetails"
        @select-episode="openEpisodeDetails" @play-episode="playEpisode" />

      <!-- Queue View -->
      <QueueView v-else-if="currentView === 'queue'" @select-episode="openEpisodeDetails" @play-episode="playEpisode" />

      <!-- Genre View -->
      <GenreView v-else-if="currentView === 'genre'" :genre="selectedGenre" :genreLabel="selectedGenreLabel"
        @select-podcast="openPodcastDetails" @select-episode="openEpisodeDetails" @play-episode="playEpisode" />

      <!-- Podcast Details (full screen overlay) -->
      <PodcastDetails v-else-if="currentView === 'podcast-details'" :uuid="selectedPodcastUuid" @back="goBack"
        @play-episode="playEpisode" @select-episode="openEpisodeDetails" />

      <!-- Episode Details (full screen overlay) -->
      <EpisodeDetails v-else-if="currentView === 'episode-details'" :uuid="selectedEpisodeUuid" @back="goBack"
        @play-episode="playEpisode" @select-podcast="openPodcastDetails" />
    </div>

    <!-- Player panel (always visible when playing) -->
    <div :class="['player-wrapper', { 'has-episode': shouldShowPlayerLayout }]">
      <PodcastPlayer v-if="podcastStore.hasCurrentEpisode" @select-podcast="openPodcastDetails"
        @select-episode="openEpisodeDetails" />
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { usePodcastStore } from '@/stores/podcastStore'
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore'
import useWebSocket from '@/services/websocket'
import { useI18n } from '@/services/i18n'
import ModalHeader from '@/components/ui/ModalHeader.vue'
import IconButton from '@/components/ui/IconButton.vue'

// Views
import HomeView from './HomeView.vue'
import SubscriptionsView from './SubscriptionsView.vue'
import SearchView from './SearchView.vue'
import QueueView from './QueueView.vue'
import GenreView from './GenreView.vue'
import PodcastDetails from './PodcastDetails.vue'
import EpisodeDetails from './EpisodeDetails.vue'
import PodcastPlayer from './PodcastPlayer.vue'

const podcastStore = usePodcastStore()
const unifiedStore = useUnifiedAudioStore()
const { on: onWsEvent } = useWebSocket()
const { t } = useI18n()

// Subscribe to podcast plugin events
onWsEvent('plugin', 'state_changed', (message) => {
  if (message.source === 'podcast') {
    podcastStore.handleStateUpdate(message.data || {})
  }
})

// Player layout visibility control (similar to RadioSource)
const shouldShowPlayerLayout = ref(false)
const stopTimer = ref(null)

// Auto-hide logic: show player while playing, stop after 5s of pause
watch(() => podcastStore.isPlaying, (isPlaying) => {
  // Clear any existing timer
  if (stopTimer.value) {
    clearTimeout(stopTimer.value)
    stopTimer.value = null
  }

  if (isPlaying && podcastStore.hasCurrentEpisode) {
    // Playing: show with animation delay for smooth CSS transition
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        shouldShowPlayerLayout.value = true
      })
    })
  } else if (!isPlaying && podcastStore.hasCurrentEpisode) {
    // Paused with an episode: stop playback after 5s
    stopTimer.value = setTimeout(async () => {
      await podcastStore.stop()
    }, 5000)
  } else if (!podcastStore.hasCurrentEpisode) {
    // No episode at all: hide immediately
    shouldShowPlayerLayout.value = false
  }
}, { immediate: true })

// Sync layout visibility with episode presence
watch(() => podcastStore.hasCurrentEpisode, (hasEpisode) => {
  if (hasEpisode && podcastStore.isPlaying) {
    // If episode appears and we're playing, show layout immediately
    shouldShowPlayerLayout.value = true
  } else if (!hasEpisode) {
    // No episode: hide layout immediately
    shouldShowPlayerLayout.value = false
  }
}, { immediate: true })

// Clean up metadata when transitioning from CONNECTED to READY
// (episode ended or stopped)
watch(() => unifiedStore.systemState.plugin_state, (newState, oldState) => {
  if (oldState === 'connected' && newState === 'ready') {
    // Clear episode metadata when playback stops
    podcastStore.clearState()
  }
}, { immediate: true })

// Navigation state
const currentView = ref('home')
const previousView = ref('home')
const selectedPodcastUuid = ref('')
const selectedEpisodeUuid = ref('')
const selectedGenre = ref('')
const selectedGenreLabel = ref('')

// Computed title and subtitle based on view
const currentTitle = computed(() => {
  switch (currentView.value) {
    case 'home':
      return 'Podcasts'
    case 'subscriptions':
      return 'Abonnements'
    case 'search':
      return 'Recherche'
    case 'queue':
      return 'File d\'attente'
    case 'genre':
      return selectedGenreLabel.value
    case 'podcast-details':
      return 'Détails Podcast'
    case 'episode-details':
      return 'Détails Épisode'
    default:
      return 'Podcasts'
  }
})

const currentSubtitle = computed(() => {
  if (currentView.value === 'genre') {
    return t('podcasts.top30')
  }
  return null
})

// Navigation methods
function goToHome() {
  previousView.value = currentView.value
  currentView.value = 'home'
}

function goToSubscriptions() {
  previousView.value = currentView.value
  currentView.value = 'subscriptions'
}

function goToSearch() {
  previousView.value = currentView.value
  currentView.value = 'search'
}

function goToQueue() {
  previousView.value = currentView.value
  currentView.value = 'queue'
}

function goToGenre(genre, label) {
  previousView.value = currentView.value
  selectedGenre.value = genre
  selectedGenreLabel.value = label
  currentView.value = 'genre'
}

function goBack() {
  // Smart back navigation
  if (currentView.value === 'podcast-details' || currentView.value === 'episode-details') {
    currentView.value = previousView.value
  } else {
    currentView.value = 'home'
  }
}

async function openPodcastDetails(podcastOrUuid) {
  previousView.value = currentView.value

  // Handle both UUID (string) and podcast object
  if (typeof podcastOrUuid === 'string') {
    // Direct UUID from subscriptions or search
    selectedPodcastUuid.value = podcastOrUuid
  } else if (podcastOrUuid && podcastOrUuid.uuid) {
    // Podcast object with UUID already resolved
    selectedPodcastUuid.value = podcastOrUuid.uuid
  } else if (podcastOrUuid && podcastOrUuid.itunes_id) {
    // Podcast object from iTunes RSS without UUID - need to lookup
    try {
      // Lookup UUID from iTunes ID (pass name for better matching)
      const params = new URLSearchParams({ name: podcastOrUuid.name || '' })
      const response = await fetch(`/api/podcast/lookup/itunes/${podcastOrUuid.itunes_id}?${params}`)
      if (response.ok) {
        const data = await response.json()
        selectedPodcastUuid.value = data.uuid
      } else {
        console.error('Failed to lookup podcast UUID from iTunes ID')
        return
      }
    } catch (error) {
      console.error('Error looking up podcast UUID:', error)
      return
    }
  } else {
    console.error('Invalid podcast data:', podcastOrUuid)
    return
  }

  currentView.value = 'podcast-details'
}

function openEpisodeDetails(uuid) {
  previousView.value = currentView.value
  selectedEpisodeUuid.value = uuid
  currentView.value = 'episode-details'
}

async function playEpisode(episode) {
  try {
    await podcastStore.play(episode.uuid)
  } catch (error) {
    console.error('Error playing episode:', error)
  }
}

// Initialize
onMounted(async () => {
  // Load settings and initial data
  await podcastStore.loadSettings()
})
</script>

<style scoped>
::-webkit-scrollbar {
  display: none;
}

.podcast-source-wrapper {
  display: flex;
  justify-content: center;
  width: 100%;
  height: 100%;
  padding: 0 var(--space-07);
  transition: all var(--transition-spring);
}

/* Main container: scrollable like radio-container */
.podcast-container {
  position: relative;
  width: 84%;
  max-height: 100%;
  display: flex;
  flex-direction: column;
  overflow-y: auto;
  padding: var(--space-07) 0;
  gap: var(--space-06);
  min-height: 0;
  flex-shrink: 0;
  touch-action: pan-y;
  transition: width 0.6s cubic-bezier(0.5, 0, 0, 1);
}

.podcast-container.with-player {
  width: calc(100% - 310px - 24px);
  margin-right: 24px;
  transition: width var(--transition-spring);
}



/* Player wrapper: matching radio's now-playing-wrapper exactly */
.player-wrapper {
  width: 0;
  max-width: 310px;
  opacity: 0;
  flex-shrink: 0;
  transform: translateX(100px);
  transition:
    width 0.6s cubic-bezier(0.5, 0, 0, 1),
    transform 0.6s cubic-bezier(0.5, 0, 0, 1),
    opacity 0.6s cubic-bezier(0.5, 0, 0, 1);
  pointer-events: none;
}

.player-wrapper.has-episode {
  width: 310px;
  max-width: 310px;
  opacity: 1;
  transform: translateX(0);
  transition:
    width var(--transition-spring),
    transform var(--transition-spring),
    opacity 0.4s ease-out;
  pointer-events: all;
}

/* Mobile: player is position fixed, so the wrapper must be transparent */
@media (max-aspect-ratio: 4/3) {
  .podcast-source-wrapper {
    padding: 0 var(--space-05);
  }

  .podcast-container {
    width: 100%;
    max-width: none;
    padding-bottom: calc(var(--space-04) + 80px);
    padding-top: var(--space-09);
  }

  .podcast-container.with-player {
    width: 100%;
    margin-right: 0;
  }

  .player-wrapper {
    display: contents;
  }

  .player-wrapper :deep(.podcast-player) {
    transform: translate(-50%, 120px);
    opacity: 0;
    transition:
      transform 0.6s cubic-bezier(0.5, 0, 0, 1),
      opacity 0.6s cubic-bezier(0.5, 0, 0, 1);
  }

  .player-wrapper.has-episode :deep(.podcast-player) {
    transform: translate(-50%, 0);
    opacity: 1;
    transition:
      transform var(--transition-spring),
      opacity 0.4s ease-out;
  }
}
</style>
