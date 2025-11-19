<template>
  <div class="podcast-source-wrapper">
    <div class="podcast-container">
      <!-- Header with navigation -->
      <ModalHeader
        :title="currentTitle"
        :showBack="currentView !== 'home'"
        icon="podcast"
        @back="goToHome"
      >
        <template #actions>
          <CircularIcon
            v-if="currentView === 'home'"
            icon="heart"
            variant="light"
            :active="false"
            @click="goToSubscriptions"
          />
          <CircularIcon
            v-if="currentView === 'home'"
            icon="search"
            variant="light"
            @click="goToSearch"
          />
          <CircularIcon
            v-if="currentView === 'home'"
            icon="list"
            variant="light"
            @click="goToQueue"
          />
        </template>
      </ModalHeader>

      <!-- Main content area -->
      <div class="content-area">
        <!-- Home View (Discovery) -->
        <HomeView
          v-if="currentView === 'home'"
          @select-podcast="openPodcastDetails"
          @select-episode="openEpisodeDetails"
          @play-episode="playEpisode"
          @browse-genre="goToGenre"
        />

        <!-- Subscriptions View -->
        <SubscriptionsView
          v-else-if="currentView === 'subscriptions'"
          @select-podcast="openPodcastDetails"
          @select-episode="openEpisodeDetails"
          @play-episode="playEpisode"
        />

        <!-- Search View -->
        <SearchView
          v-else-if="currentView === 'search'"
          @select-podcast="openPodcastDetails"
          @select-episode="openEpisodeDetails"
          @play-episode="playEpisode"
        />

        <!-- Queue View -->
        <QueueView
          v-else-if="currentView === 'queue'"
          @select-episode="openEpisodeDetails"
          @play-episode="playEpisode"
        />

        <!-- Genre View -->
        <GenreView
          v-else-if="currentView === 'genre'"
          :genre="selectedGenre"
          :genreLabel="selectedGenreLabel"
          @select-podcast="openPodcastDetails"
          @select-episode="openEpisodeDetails"
          @play-episode="playEpisode"
        />

        <!-- Podcast Details (full screen overlay) -->
        <PodcastDetails
          v-else-if="currentView === 'podcast-details'"
          :uuid="selectedPodcastUuid"
          @back="goBack"
          @play-episode="playEpisode"
          @select-episode="openEpisodeDetails"
        />

        <!-- Episode Details (full screen overlay) -->
        <EpisodeDetails
          v-else-if="currentView === 'episode-details'"
          :uuid="selectedEpisodeUuid"
          @back="goBack"
          @play-episode="playEpisode"
          @select-podcast="openPodcastDetails"
        />
      </div>
    </div>

    <!-- Player panel (always visible when playing) -->
    <div :class="['player-wrapper', { 'has-episode': podcastStore.hasCurrentEpisode }]">
      <PodcastPlayer
        v-if="podcastStore.hasCurrentEpisode"
        @select-podcast="openPodcastDetails"
        @select-episode="openEpisodeDetails"
      />
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { usePodcastStore } from '@/stores/podcastStore'
import useWebSocket from '@/services/websocket'
import ModalHeader from '@/components/ui/ModalHeader.vue'
import CircularIcon from '@/components/ui/CircularIcon.vue'

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
const { on: onWsEvent } = useWebSocket()

// Subscribe to podcast plugin events
onWsEvent('plugin', 'state_changed', (message) => {
  if (message.source === 'podcast') {
    podcastStore.handleStateUpdate(message.data || {})
  }
})

// Navigation state
const currentView = ref('home')
const previousView = ref('home')
const selectedPodcastUuid = ref('')
const selectedEpisodeUuid = ref('')
const selectedGenre = ref('')
const selectedGenreLabel = ref('')

// Computed title based on view
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

/* Wrapper: similar to radio-source-wrapper */
.podcast-source-wrapper {
  display: flex;
  justify-content: center;
  width: 100%;
  height: 100%;
  gap: var(--space-06);
  padding: 0 var(--space-06);
}

/* Main container: scrollable like radio-container */
.podcast-container {
  position: relative;
  width: 100%;
  max-width: 800px;
  max-height: 100%;
  display: flex;
  flex-direction: column;
  overflow-y: auto;
  padding: var(--space-07) 0;
  gap: var(--space-04);
  min-height: 0;
  flex: 1 1 auto;
  flex-shrink: 1;
  touch-action: pan-y;
}

/* Content area: no scroll, natural height */
.content-area {
  padding: 0 var(--space-04);
}

/* Player wrapper: similar to now-playing-wrapper */
.player-wrapper {
  width: 0;
  opacity: 0;
  overflow: hidden;
  flex-shrink: 0;
  transform: translateX(100px);
}

.player-wrapper.has-episode {
  width: 310px;
  opacity: 1;
  overflow: visible;
  transform: translateX(0);
  transition:
    width var(--transition-spring),
    transform var(--transition-spring),
    opacity 0.4s ease;
}

/* Mobile: player is position fixed, so the wrapper must be transparent */
@media (max-aspect-ratio: 4/3) {
  .podcast-container {
    max-width: none;
    padding-bottom: calc(var(--space-04) + 80px);
    padding-top: var(--space-09);
  }

  .player-wrapper {
    display: contents;
  }

  .player-wrapper.has-episode :deep(.podcast-player) {
    transition:
      transform var(--transition-spring),
      opacity 0.4s ease;
  }
}
</style>
