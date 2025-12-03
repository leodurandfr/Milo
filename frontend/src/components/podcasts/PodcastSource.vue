<template>
  <AudioSourceLayout
    :show-player="shouldShowPlayerLayout && !hasCredentialsError"
    :header-title="hasCredentialsError ? t('podcasts.podcasts') : currentTitle"
    :header-subtitle="hasCredentialsError ? null : currentSubtitle"
    :header-show-back="!hasCredentialsError && canGoBack"
    header-icon="podcast"
    header-variant="background-neutral"
    :header-actions-key="currentView"
    :content-key="hasCredentialsError ? 'credentials' : currentView"
    :player-mobile-height="184"
    @header-back="goBack"
  >
    <!-- Header actions (only when not in credentials error and on home view) -->
    <template v-if="!hasCredentialsError && currentView === 'home'" #header-actions="{ iconVariant }">
      <IconButton icon="heartOff" :variant="iconVariant" :active="false" @click="goToSubscriptions" />
      <IconButton icon="search" :variant="iconVariant" @click="goToSearch" />
      <IconButton icon="queue" :variant="iconVariant" @click="goToQueue" />
    </template>

    <!-- Content slot: scrollable views -->
    <template #content>
      <div class="podcast-content" :class="{ 'search-spacing': currentView === 'search' && !hasCredentialsError }">
        <!-- Credentials Required -->
        <CredentialsRequired v-if="hasCredentialsError" key="credentials" @configure="openPodcastSettings" />

        <!-- Home View (Discovery) -->
        <HomeView v-else-if="currentView === 'home'" key="home" @select-podcast="openPodcastDetails"
          @select-episode="openEpisodeDetails" @play-episode="playEpisode" @browse-genre="goToGenre" />

        <!-- Subscriptions View -->
        <SubscriptionsView v-else-if="currentView === 'subscriptions'" key="subscriptions"
          @select-podcast="openPodcastDetails" @select-episode="openEpisodeDetails" @play-episode="playEpisode" />

        <!-- Search View -->
        <SearchView v-else-if="currentView === 'search'" key="search" @select-podcast="openPodcastDetails"
          @select-episode="openEpisodeDetails" @play-episode="playEpisode" />

        <!-- Queue View -->
        <QueueView v-else-if="currentView === 'queue'" key="queue" @select-episode="openEpisodeDetails"
          @play-episode="playEpisode" @select-podcast="openPodcastDetails" />

        <!-- Genre View -->
        <GenreView v-else-if="currentView === 'genre'" key="genre" :genre="selectedGenre"
          :genreLabel="selectedGenreLabel" :loadingPodcastId="loadingPodcastId"
          @select-podcast="openPodcastDetails" @select-episode="openEpisodeDetails"
          @play-episode="playEpisode" />

        <!-- Podcast Details (full screen overlay) -->
        <PodcastDetails v-else-if="currentView === 'podcast-details'" key="podcast-details"
          :uuid="selectedPodcastUuid" @back="goBack" @play-episode="playEpisode"
          @select-episode="openEpisodeDetails" />

        <!-- Episode Details (full screen overlay) -->
        <EpisodeDetails v-else-if="currentView === 'episode-details'" key="episode-details"
          :uuid="selectedEpisodeUuid" @back="goBack" @play-episode="playEpisode"
          @select-podcast="openPodcastDetails" />
      </div>
    </template>

    <!-- Player slot: AudioPlayer component -->
    <template #player="{ playerWidth }">
      <AudioPlayer :visible="shouldShowPlayerLayout" source="podcast" :artwork="episodeImage" :title="episodeName"
        :subtitle="podcastName" :is-playing="isCurrentlyPlaying" :is-loading="isBuffering" :width="playerWidth">
        <!-- Progress bar (seekable) -->
        <template #progress>
          <div @click.stop>
            <ProgressBar :currentPosition="podcastStore.currentPosition" :duration="podcastStore.currentDuration"
              :progressPercentage="progressPercentage" @seek="handleSeek" />
          </div>
        </template>

        <!-- Podcast controls with speed and seek -->
        <template #controls>
          <!-- Speed selector -->
          <div class="speed-selector" @click.stop>
            <Dropdown v-model="selectedSpeed" :options="speedOptions" variant="minimal"
              @change="handleSpeedChange" />
          </div>

          <!-- Playback controls -->
          <div class="playback-controls" @click.stop>
            <IconButton icon="rewind15" variant="on-dark" size="small" @click="seekBackward" />

            <!-- Play/Pause button with loading state -->
            <IconButton :icon="isCurrentlyPlaying ? 'pause' : 'play'" variant="on-dark" size="medium"
              :loading="isBuffering" @click="togglePlayPause" />

            <IconButton icon="forward30" variant="on-dark" size="small" @click="seekForward" />
          </div>
        </template>
      </AudioPlayer>
    </template>
  </AudioSourceLayout>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount, watch, inject } from 'vue'
import { usePodcastStore } from '@/stores/podcastStore'
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore'
import { useSettingsStore } from '@/stores/settingsStore'
import { useNavigationStack } from '@/composables/useNavigationStack'
import { useI18n } from '@/services/i18n'
import IconButton from '@/components/ui/IconButton.vue'
import AudioPlayer from '@/components/audio/AudioPlayer.vue'
import AudioSourceLayout from '@/components/audio/AudioSourceLayout.vue'
import Dropdown from '@/components/ui/Dropdown.vue'
import episodePlaceholder from '@/assets/podcasts/podcast-placeholder.jpg'

// Views
import HomeView from './HomeView.vue'
import SubscriptionsView from './SubscriptionsView.vue'
import SearchView from './SearchView.vue'
import QueueView from './QueueView.vue'
import GenreView from './GenreView.vue'
import PodcastDetails from './PodcastDetails.vue'
import EpisodeDetails from './EpisodeDetails.vue'
import ProgressBar from './ProgressBar.vue'
import CredentialsRequired from './CredentialsRequired.vue'

const podcastStore = usePodcastStore()
const unifiedStore = useUnifiedAudioStore()
const settingsStore = useSettingsStore()
const { t } = useI18n()

// Navigation with stack
const { currentView, currentParams, canGoBack, push, back, reset } = useNavigationStack('home')

// Inject openSettings from App.vue
const openSettings = inject('openSettings')

// Credentials status check
const hasCredentialsError = computed(() => {
  const status = settingsStore.podcastCredentialsStatus
  return status === 'missing' || status === 'invalid' || status === 'rate_limited'
})

// Open podcast settings - navigates directly to podcast settings view
function openPodcastSettings() {
  if (openSettings) {
    openSettings('podcast')
  }
}

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
  } else if (!isPodcastActive || newState === 'inactive' || newState === 'ready') {
    // Different source active, plugin inactive, or ready (no episode) - hide immediately
    shouldShowPlayerLayout.value = false
  }
}, { immediate: true })

// Watch shouldShowPlayerLayout to clear displayEpisode after fade-out animation
watch(shouldShowPlayerLayout, (isVisible, wasVisible) => {
  // Detect transition from visible → hidden (fade-out animation starts)
  if (wasVisible && !isVisible) {
    // Wait for fade-out animation to complete (600ms), then clear display metadata
    setTimeout(() => {
      podcastStore.clearDisplayEpisode()
    }, 600)
  }
})

// Watcher: triggers auto-stop after 5s when paused
watch(() => [isCurrentlyPlaying.value, isBuffering.value, podcastStore.hasCurrentEpisode],
  ([playing, buffering, hasEpisode]) => {
    // Clear any existing timer
    if (stopTimer.value) {
      clearTimeout(stopTimer.value)
      stopTimer.value = null
    }

    // If paused with an episode: schedule auto-stop after 5s
    if (!playing && !buffering && hasEpisode) {
      stopTimer.value = setTimeout(async () => {
        // Hide the player and stop playback
        shouldShowPlayerLayout.value = false
        // Only call stop() if still not playing (user might have resumed)
        if (!isCurrentlyPlaying.value && !isBuffering.value) {
          await podcastStore.stop()
        }
      }, 5000)
    }
  }, { immediate: true })

// Navigation params (stored separately since composable handles view state)
const selectedPodcastUuid = computed(() => currentParams.value.podcastUuid || '')
const selectedEpisodeUuid = computed(() => currentParams.value.episodeUuid || '')
const selectedGenre = computed(() => currentParams.value.genre || '')
const selectedGenreLabel = computed(() => currentParams.value.genreLabel || '')

// Loading state for podcast lookup (iTunes ID → UUID conversion)
const loadingPodcastId = ref(null)

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

// Clear search when navigating back to home
watch(currentView, (newView) => {
  if (newView === 'home') {
    podcastStore.clearSearch()
  }
})

// Navigation methods using composable
function goToHome() {
  reset()
}

function goToSubscriptions() {
  push('subscriptions')
}

function goToSearch() {
  push('search')
}

function goToQueue() {
  push('queue')
}

function goToGenre(genre, label) {
  push('genre', { genre, genreLabel: label })
}

function goBack() {
  back()
}

async function openPodcastDetails(podcastOrUuid) {
  let uuid = ''

  // Handle both UUID (string) and podcast object
  if (typeof podcastOrUuid === 'string') {
    // Direct UUID from subscriptions or search
    uuid = podcastOrUuid
  } else if (podcastOrUuid && podcastOrUuid.uuid) {
    // Podcast object with UUID already resolved
    uuid = podcastOrUuid.uuid
  } else if (podcastOrUuid && podcastOrUuid.itunes_id) {
    // Podcast object from iTunes RSS without UUID - need to lookup
    // Set loading state (use itunes_id as identifier)
    loadingPodcastId.value = podcastOrUuid.itunes_id
    try {
      // Lookup UUID from iTunes ID (pass name for better matching)
      const params = new URLSearchParams({ name: podcastOrUuid.name || '' })
      const response = await fetch(`/api/podcast/lookup/itunes/${podcastOrUuid.itunes_id}?${params}`)
      if (response.ok) {
        const data = await response.json()
        uuid = data.uuid
      } else {
        console.error('Failed to lookup podcast UUID from iTunes ID')
        loadingPodcastId.value = null
        return
      }
    } catch (error) {
      console.error('Error looking up podcast UUID:', error)
      loadingPodcastId.value = null
      return
    }
    // Clear loading state after successful lookup
    loadingPodcastId.value = null
  } else {
    console.error('Invalid podcast data:', podcastOrUuid)
    return
  }

  push('podcast-details', { podcastUuid: uuid })
}

function openEpisodeDetails(uuid) {
  push('episode-details', { episodeUuid: uuid })
}

async function playEpisode(episode) {
  try {
    await podcastStore.play(episode.uuid)
  } catch (error) {
    console.error('Error playing episode:', error)
  }
}

// ===== Player controls and data (moved from PodcastPlayer.vue) =====

// Episode artwork - use displayEpisode for fade-out animation preservation
const episodeImage = computed(() => {
  return podcastStore.displayEpisode?.image_url || episodePlaceholder
})

// Episode name - use displayEpisode for fade-out animation preservation
const episodeName = computed(() => {
  return podcastStore.displayEpisode?.name || t('podcasts.noEpisode')
})

// Podcast name - use displayEpisode for fade-out animation preservation
const podcastName = computed(() => {
  return podcastStore.displayEpisode?.podcast?.name || ''
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
})

// Cleanup on unmount
onBeforeUnmount(() => {
  // Clear stop timer
  if (stopTimer.value) {
    clearTimeout(stopTimer.value)
    stopTimer.value = null
  }
  // Clear search state when leaving Podcasts module
  podcastStore.clearSearch()
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
  gap: var(--space-04);
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

:deep(.dropdown-trigger--transparent) {
  min-width: 48px;
  padding: var(--space-02) 0;
}
</style>
