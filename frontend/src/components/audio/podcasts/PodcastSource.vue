<template>
  <AudioSourceLayout :show-player="shouldShowPlayerLayout">
    <!-- Content slot: scrollable views -->
    <template #content>
      <div class="podcast-content">
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
    </template>

    <!-- Player slot: AudioPlayer component -->
    <template #player>
      <AudioPlayer
        :visible="shouldShowPlayerLayout"
        source="podcast"
        :artwork="episodeImage"
        :title="episodeName"
        :subtitle="podcastName"
        :is-playing="isCurrentlyPlaying"
        :is-loading="isBuffering"
        :expandable="true"
        :expanded="isPlayerExpanded"
        @toggle-expanded="isPlayerExpanded = !isPlayerExpanded"
      >
        <!-- Progress bar (seekable) -->
        <template #progress>
          <div @click.stop>
            <ProgressBar
              :currentPosition="podcastStore.currentPosition"
              :duration="podcastStore.currentDuration"
              :progressPercentage="progressPercentage"
              @seek="handleSeek"
            />
          </div>
        </template>

        <!-- Podcast controls with speed and seek -->
        <template #controls>
          <!-- Speed selector -->
          <div class="speed-selector" @click.stop>
            <Dropdown
              v-model="selectedSpeed"
              :options="speedOptions"
              variant="transparent"
              size="small"
              @change="handleSpeedChange"
            />
          </div>

          <!-- Playback controls -->
          <div class="playback-controls" @click.stop>
            <IconButton icon="rewind15" variant="dark" size="small" @click="seekBackward" />

            <!-- Play/Pause button with loading state -->
            <IconButton
              :icon="isCurrentlyPlaying ? 'pause' : 'play'"
              variant="dark"
              size="large"
              :loading="isBuffering"
              @click="togglePlayPause"
            />

            <IconButton icon="forward30" variant="dark" size="small" @click="seekForward" />
          </div>
        </template>
      </AudioPlayer>
    </template>
  </AudioSourceLayout>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount, watch } from 'vue'
import { usePodcastStore } from '@/stores/podcastStore'
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore'
import useWebSocket from '@/services/websocket'
import { useI18n } from '@/services/i18n'
import ModalHeader from '@/components/ui/ModalHeader.vue'
import IconButton from '@/components/ui/IconButton.vue'
import AudioPlayer from '@/components/ui/AudioPlayer.vue'
import AudioSourceLayout from '@/components/ui/AudioSourceLayout.vue'
import Dropdown from '@/components/ui/Dropdown.vue'
import episodePlaceholder from '@/assets/podcasts/episode-placeholder.jpg'

// Views
import HomeView from './HomeView.vue'
import SubscriptionsView from './SubscriptionsView.vue'
import SearchView from './SearchView.vue'
import QueueView from './QueueView.vue'
import GenreView from './GenreView.vue'
import PodcastDetails from './PodcastDetails.vue'
import EpisodeDetails from './EpisodeDetails.vue'
import ProgressBar from './ProgressBar.vue'

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

// Player layout visibility control - manual ref for animation control
const shouldShowPlayerLayout = ref(false)

// Expanded player state
const isPlayerExpanded = ref(false)

// Auto-stop timer: stops playback after 5 seconds of pause
const stopTimer = ref(null)

// Watch plugin_state to show player when connected
watch(() => unifiedStore.systemState.plugin_state, (newState) => {
  const isPodcastActive = unifiedStore.systemState.active_source === 'podcast'

  if (isPodcastActive && newState === 'connected') {
    // Show player when connected (with smooth entrance)
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        shouldShowPlayerLayout.value = true
      })
    })
  } else if (!isPodcastActive || newState === 'inactive') {
    // Different source active or plugin inactive - hide immediately
    shouldShowPlayerLayout.value = false
  }
}, { immediate: true })

// Watcher: triggers auto-stop after 5s when paused
watch(() => [isCurrentlyPlaying.value, isBuffering.value, podcastStore.hasCurrentEpisode, isPlayerExpanded.value],
  ([playing, buffering, hasEpisode, expanded]) => {
    // Clear any existing timer
    if (stopTimer.value) {
      clearTimeout(stopTimer.value)
      stopTimer.value = null
    }

    // If paused with an episode: schedule auto-stop after 5s
    if (!playing && !buffering && hasEpisode) {
      stopTimer.value = setTimeout(async () => {
        // If player is expanded, collapse it first
        if (expanded) {
          isPlayerExpanded.value = false
          // Wait for collapse animation to complete (600ms), then hide
          setTimeout(() => {
            shouldShowPlayerLayout.value = false
            // Then stop after hide animation (600ms)
            setTimeout(async () => {
              if (!isCurrentlyPlaying.value && !isBuffering.value) {
                await podcastStore.stop()
              }
            }, 600)
          }, 600)
        } else {
          // If compact, just hide the player (triggers 0.6s exit animation)
          shouldShowPlayerLayout.value = false

          // Wait for animation to complete (600ms), then stop
          setTimeout(async () => {
            // Only call stop() if still not playing (user might have resumed)
            if (!isCurrentlyPlaying.value && !isBuffering.value) {
              await podcastStore.stop()
            }
          }, 600)
        }
      }, 5000)
    }
  }, { immediate: true })

// Sync metadata from unifiedStore (same pattern as RadioSource)
watch(() => unifiedStore.systemState.metadata, (newMetadata) => {
  if (unifiedStore.systemState.active_source === 'podcast' && newMetadata) {
    podcastStore.handleStateUpdate(newMetadata)
  }
}, { immediate: true, deep: true })

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
      return t('podcasts.podcasts')
    case 'subscriptions':
      return t('podcasts.subscriptions')
    case 'search':
      return t('podcasts.search')
    case 'queue':
      return t('podcasts.queue')
    case 'genre':
      return selectedGenreLabel.value
    case 'podcast-details':
      return t('podcasts.podcastDetails')
    case 'episode-details':
      return t('podcasts.episodeDetails')
    default:
      return t('podcasts.podcasts')
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

// ===== Player controls and data (moved from PodcastPlayer.vue) =====

// Episode artwork
const episodeImage = computed(() => {
  return podcastStore.currentEpisode?.image_url || episodePlaceholder
})

// Episode name
const episodeName = computed(() => {
  return podcastStore.currentEpisode?.name || t('podcasts.noEpisode')
})

// Podcast name
const podcastName = computed(() => {
  return podcastStore.currentEpisode?.podcast?.name || ''
})

// Progress percentage
const progressPercentage = computed(() => {
  if (!podcastStore.currentDuration || podcastStore.currentDuration === 0) {
    return 0
  }
  return (podcastStore.currentPosition / podcastStore.currentDuration) * 100
})

// Speed control
const speeds = [0.5, 0.75, 1, 1.25, 1.5, 2]

const speedOptions = computed(() =>
  speeds.map(speed => ({
    label: `${speed}x`,
    value: String(speed)
  }))
)

const selectedSpeed = computed({
  get: () => String(podcastStore.playbackSpeed || 1),
  set: () => { } // Handled by @change event
})

// Playback controls
async function togglePlayPause() {
  if (isCurrentlyPlaying.value) {
    await podcastStore.pause()
  } else {
    await podcastStore.resume()
  }
}

async function seekBackward() {
  const newPosition = Math.max(0, podcastStore.currentPosition - 15)
  await podcastStore.seek(newPosition)
}

async function seekForward() {
  const newPosition = Math.min(
    podcastStore.currentDuration,
    podcastStore.currentPosition + 30
  )
  await podcastStore.seek(newPosition)
}

async function handleSeek(position) {
  await podcastStore.seek(position)
}

async function handleSpeedChange(speedValue) {
  const speed = parseFloat(speedValue)
  await podcastStore.setSpeed(speed)
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
  // Clear stop timer
  if (stopTimer.value) {
    clearTimeout(stopTimer.value)
    stopTimer.value = null
  }
})
</script>

<style scoped>
::-webkit-scrollbar {
  display: none;
}

/* Podcast content: wraps the views inside AudioSourceLayout's content slot */
.podcast-content {
  display: flex;
  flex-direction: column;
  gap: var(--space-06);
  width: 100%;
}

/* Player control styles (from PodcastPlayer.vue) */
.speed-selector {
  display: flex;
  align-items: center;
  position: absolute;
  left: 0;
}

.speed-selector :deep(.dropdown) {
  width: auto;
  flex: none;
}

.speed-selector :deep(.dropdown-menu) {
  min-width: 100px;
}

/* Compact playback controls on mobile */
@media (max-aspect-ratio: 4/3) {
  .playback-controls {
    gap: var(--space-01);
  }
}
</style>
