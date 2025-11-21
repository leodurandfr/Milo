<template>
  <div class="podcast-source-wrapper">
    <div class="podcast-container" :class="{ 'with-player': shouldShowPlayerLayout }">
      <!-- Header with navigation -->
      <ModalHeader :title="currentTitle" :subtitle="currentSubtitle" :showBack="currentView !== 'home'" icon="podcast"
        variant="background-neutral" @back="goToHome">
        <template #actions="{ iconVariant }">
          <IconButton v-if="currentView === 'home'" icon="heart" :variant="iconVariant" :active="false"
            @click="goToSubscriptions" />
          <IconButton v-if="currentView === 'home'" icon="search" :variant="iconVariant" @click="goToSearch" />
          <IconButton v-if="currentView === 'home'" icon="list" :variant="iconVariant" @click="goToQueue" />
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
import { ref, computed, onMounted, onBeforeUnmount, watch } from 'vue'
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

// Playback state - Read DIRECTLY from unifiedStore (single source of truth)
const isCurrentlyPlaying = computed(() => {
  // Check that the active source is Podcast
  if (unifiedStore.systemState.active_source !== 'podcast') {
    return false
  }
  // Use backend state via WebSocket
  return unifiedStore.systemState.metadata?.is_playing || false
})

// Buffering state - Read DIRECTLY from unifiedStore (single source of truth)
const isBuffering = computed(() => {
  // Check that the active source is Podcast
  if (unifiedStore.systemState.active_source !== 'podcast') {
    return false
  }
  // Use backend state via WebSocket
  return unifiedStore.systemState.metadata?.is_buffering || false
})

// Player layout visibility control (same pattern as RadioSource)
const shouldShowPlayerLayout = ref(false)
const stopTimer = ref(null)
const cleanupTimer = ref(null)

// Combined watcher: handles visibility, animation, and auto-stop timer
watch(() => [isCurrentlyPlaying.value, isBuffering.value, podcastStore.hasCurrentEpisode],
  ([playing, buffering, hasEpisode]) => {
    // Clear any existing timers
    if (stopTimer.value) {
      clearTimeout(stopTimer.value)
      stopTimer.value = null
    }
    if (cleanupTimer.value) {
      clearTimeout(cleanupTimer.value)
      cleanupTimer.value = null
    }

    if ((playing || buffering) && hasEpisode) {
      // Playing/buffering: show player with animation
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          shouldShowPlayerLayout.value = true
        })
      })
    } else if (!playing && !buffering && hasEpisode) {
      // Paused with an episode: hide visually after 5s, then stop after animation completes
      stopTimer.value = setTimeout(() => {
        // Step 1: Hide visually (triggers CSS animation)
        shouldShowPlayerLayout.value = false

        // Step 2: Call stop() after animation completes (600ms transition time)
        cleanupTimer.value = setTimeout(async () => {
          await podcastStore.stop()
        }, 700) // Slightly longer than CSS transition (0.6s)
      }, 5000)
    } else if (!hasEpisode) {
      // No episode: hide immediately
      shouldShowPlayerLayout.value = false
    }
  }, { immediate: true })

// Stable layout during episode changes (same pattern as Radio lines 466-474)
// Pre-initialize layout if episode is already playing (prevents animation on refresh)
watch(() => podcastStore.currentEpisode, (newEpisode) => {
  if (newEpisode && (isCurrentlyPlaying.value || isBuffering.value)) {
    // Lock the layout immediately (no animation on refresh)
    shouldShowPlayerLayout.value = true
  }
  // Note: Don't unlock here - let the main watcher handle unlocking
}, { immediate: true })

// Sync metadata from unifiedStore (same pattern as RadioSource)
watch(() => unifiedStore.systemState.metadata, (newMetadata) => {
  if (unifiedStore.systemState.active_source === 'podcast' && newMetadata) {
    podcastStore.handleStateUpdate(newMetadata)
  }
}, { immediate: true, deep: true })

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

  // IMPORTANT: Sync currentEpisode from current backend state (same pattern as RadioSource)
  if (unifiedStore.systemState.active_source === 'podcast' && unifiedStore.systemState.metadata) {
    console.log('🎙️ Syncing currentEpisode from existing state on mount')
    podcastStore.handleStateUpdate(unifiedStore.systemState.metadata)
  }
})

// Cleanup on unmount
onBeforeUnmount(() => {
  // Clear all timers
  if (stopTimer.value) {
    clearTimeout(stopTimer.value)
    stopTimer.value = null
  }
  if (cleanupTimer.value) {
    clearTimeout(cleanupTimer.value)
    cleanupTimer.value = null
  }
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
  width: calc(100% - 310px - var(--space-06));
  /* margin-right removed - gap created by width calculation */
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
