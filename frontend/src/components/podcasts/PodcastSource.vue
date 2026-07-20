<template>
  <AudioSourceLayout ref="audioLayoutRef" :show-player="shouldShowPlayerLayout"
    :header-title="currentTitle"
    :header-subtitle="currentSubtitle"
    :header-show-back="canGoBack" header-icon="podcast" header-variant="background-neutral"
    :header-actions-key="currentView" :content-key="currentView"
    :player-mobile-height="144" :pending-scroll-restore="pendingScrollRestore" gradient="podcast" @header-back="goBack"
    @scroll-restored="onScrollRestored">
    <!-- Header actions (only on home view) -->
    <template v-if="currentView === 'home'" #header-actions="{ iconVariant }">
      <IconButton icon="heartOff" :variant="iconVariant" :active="false" @click="goToSubscriptions" />
      <IconButton icon="search" :variant="iconVariant" @click="goToSearch" />
      <IconButton icon="queue" :variant="iconVariant" @click="goToQueue" />
    </template>

    <!-- Content slot: scrollable views -->
    <template #content>
        <!-- Home View (Discovery) -->
        <HomeView v-if="currentView === 'home'" key="home" :loadingPodcastId="loadingPodcastId"
          @select-podcast="openPodcastDetails"
          @select-episode="openEpisodeDetails" @play-episode="playEpisode" @browse-genre="goToGenre" />

        <!-- Subscriptions View -->
        <SubscriptionsView v-else-if="currentView === 'subscriptions'" key="subscriptions"
          @select-podcast="openPodcastDetails" @select-episode="openEpisodeDetails" @play-episode="playEpisode" />

        <!-- Search View -->
        <SearchView v-else-if="currentView === 'search'" key="search" :loadingPodcastId="loadingPodcastId"
          @select-podcast="openPodcastDetails" />

        <!-- Queue View -->
        <QueueView v-else-if="currentView === 'queue'" key="queue" @select-episode="openEpisodeDetails"
          @play-episode="playEpisode" @select-podcast="openPodcastDetails" />

        <!-- Genre View -->
        <GenreView v-else-if="currentView === 'genre'" key="genre" :genre="selectedGenre"
          :genreLabel="selectedGenreLabel" :loadingPodcastId="loadingPodcastId" @select-podcast="openPodcastDetails"
          @select-episode="openEpisodeDetails" @play-episode="playEpisode" />

        <!-- Podcast Details (full screen overlay) -->
        <PodcastDetails v-else-if="currentView === 'podcast-details'" key="podcast-details" :uuid="selectedPodcastUuid"
          @play-episode="playEpisode" @select-episode="openEpisodeDetails" />

        <!-- Episode Details (full screen overlay) -->
        <EpisodeDetails v-else-if="currentView === 'episode-details'" key="episode-details" :uuid="selectedEpisodeUuid"
          @play-episode="playEpisode" @select-podcast="openPodcastDetails" />
    </template>

    <!-- Player slot: AudioPlayer component -->
    <template #player>
      <AudioPlayer :visible="shouldShowPlayerLayout" source="podcast" :artwork="episodeImage" :title="episodeName"
        :is-playing="isCurrentlyPlaying" :is-loading="isBuffering" swipe-enabled
        @swipe-next="seekForward" @swipe-prev="seekBackward">
        <!-- Track info: podcast name kicker + episode title. Desktop uses the shared
             PlayerInfoText; mobile shows the same title/podcast-name pair as its own
             compact title/subtitle lines. -->
        <template #info>
          <PlayerInfoText class="desktop-only" :kicker="podcastName" :title="episodeName" />
          <p class="player-title text-body mobile-only">{{ episodeName }}</p>
          <p v-if="podcastName" class="player-subtitle text-body mobile-only">{{ podcastName }}</p>
        </template>

        <!-- Progress bar (seekable) -->
        <template #progress>
          <div @click.stop>
            <ProgressBar :currentPosition="currentPositionSec" :duration="currentDurationSec"
              :progressPercentage="livePercent" @seek="handleSeek" />
          </div>
        </template>

        <!-- Podcast controls: play/pause everywhere; seek buttons + speed selector are
             desktop-only — on mobile the mini-player's swipe gesture covers +30s (right)
             / -15s (left), speed moves into the future expanded mini-player view. -->
        <template #controls>
          <div class="playback-controls" @click.stop>
            <IconButton icon="rewind15" variant="on-dark" size="small" class="desktop-only" @click="seekBackward" />

            <IconButton :icon="isCurrentlyPlaying ? 'pause' : 'play'" variant="on-dark" size="medium"
              :loading="isBuffering" @click="togglePlayPause" />

            <IconButton icon="forward30" variant="on-dark" size="small" class="desktop-only" @click="seekForward" />
          </div>

          <div class="speed-selector desktop-only" @click.stop>
            <Dropdown v-model="selectedSpeed" :options="speedOptions" variant="minimal" @change="handleSpeedChange" />
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
import { useNavigationStack } from '@/composables/useNavigationStack'
import { useSourcePlaybackVisibility } from '@/composables/useSourcePlaybackVisibility'
import { useSourceProgress } from '@/composables/useSourceProgress'
import { useTimer } from '@/composables/useTimer'
import { useI18n } from '@/services/i18n'
import { apiCall } from '@/services/apiCall'
import { logger } from '@/services/logger'
import IconButton from '@/components/ui/IconButton.vue'
import AudioPlayer from '@/components/audio/AudioPlayer.vue'
import AudioSourceLayout from '@/components/audio/AudioSourceLayout.vue'
import PlayerInfoText from '@/components/audio/PlayerInfoText.vue'
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

const podcastStore = usePodcastStore()
const unifiedStore = useUnifiedAudioStore()
const { t } = useI18n()

// Ref to AudioSourceLayout — used to access its scroll container ($el) for position save/restore
const audioLayoutRef = ref(null)
const layoutScrollRef = computed(() => audioLayoutRef.value?.$el ?? null)

// Navigation with stack — scrollElRef enables scroll position save on push() and restore on back()
const { currentView, currentParams, canGoBack, push, back, pendingScrollRestore } =
  useNavigationStack('home', { scrollElRef: layoutScrollRef })

// Playback state + player visibility (shared logic via composable).
// Visibility follows the backend's source_state transitions — when the backend
// auto-stops after `audio.auto_stop_delay`, source_state flips to
// 'waiting' and the player fades out. clearDisplayEpisode runs once the fade
// animation is done so the artwork stays visible during the fade.
const timer = useTimer()

const { isPlaying: isCurrentlyPlaying, isBuffering, shouldShowPlayer: shouldShowPlayerLayout } =
  useSourcePlaybackVisibility('podcast', {
    onFadeOutStart: () => {
      timer.setTimeout(() => podcastStore.clearDisplayEpisode(), 600)
    }
  })

// Live position with 100ms local interpolation between backend syncs.
// Reads position/duration (in ms) from unifiedStore.systemState.metadata —
// kept current by state_changed events and source.position_update.
const {
  currentPosition: positionMs,
  duration: durationMs,
  progressPercentage: livePercent,
  seekTo,
} = useSourceProgress('podcast')

// ProgressBar + seekBackward/seekForward operate in seconds.
const currentPositionSec = computed(() => Math.floor((positionMs.value || 0) / 1000))
const currentDurationSec = computed(() => Math.floor((durationMs.value || 0) / 1000))

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

function onScrollRestored() {
  pendingScrollRestore.value = null
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
    // Podcast object from iTunes RSS without UUID - need to lookup.
    // A miss means Podcast Index doesn't index this podcast (expected for some
    // charts entries), so log it as info and tell the user instead of failing silently.
    loadingPodcastId.value = podcastOrUuid.itunes_id
    const result = await apiCall.get(`/api/podcast/lookup/itunes/${podcastOrUuid.itunes_id}`, {
      category: 'podcast',
      message: 'Podcast not found in catalog',
      params: { name: podcastOrUuid.name || '', artist: podcastOrUuid.artist || '' },
      logLevel: 'info',
    })
    loadingPodcastId.value = null
    if (!result.ok || !result.data?.uuid) {
      unifiedStore.transientNotice = {
        title: t('podcasts.notAvailable'),
        detail: podcastOrUuid.name || null,
      }
      return
    }
    uuid = result.data.uuid
  } else {
    logger.error('podcast', 'Invalid podcast data', podcastOrUuid)
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
    logger.error('podcast', 'Error playing episode', error)
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

// Speed control — canonical list owned by backend, fetched at mount time
const speedOptions = computed(() =>
  podcastStore.playbackSpeeds.map(speed => ({
    label: `${speed}x`,
    value: String(speed)
  }))
)

const selectedSpeed = computed({
  get: () => String(podcastStore.playbackSpeed || 1),
  set: () => { } // Handled by @change event
})

async function togglePlayPause() {
  if (isCurrentlyPlaying.value) {
    await podcastStore.pause()
  } else {
    await podcastStore.resume()
  }
}

// All seeks go through useSourceProgress.seekTo (ms): it sets localPosition
// optimistically and suppresses the next WS sync, so the bar jumps instantly
// instead of waiting for the backend round-trip.
async function seekBackward() {
  await seekTo(Math.max(0, positionMs.value - 15000))
}

async function seekForward() {
  await seekTo(Math.min(durationMs.value, positionMs.value + 30000))
}

// ProgressBar emits the target position in seconds.
async function handleSeek(positionSec) {
  await seekTo(positionSec * 1000)
}

async function handleSpeedChange(speedValue) {
  const speed = parseFloat(speedValue)
  await podcastStore.setSpeed(speed)
}

onMounted(async () => {
  // Load settings and initial data
  await podcastStore.loadSettings()
  podcastStore.loadPlaybackSpeeds()
})

onBeforeUnmount(() => {
  podcastStore.clearSearch()
})
</script>

<style scoped>
::-webkit-scrollbar {
  display: none;
}

/* Player control styles (from PodcastPlayer.vue) */
.speed-selector {
  display: flex;
  align-items: center;
  position: absolute;
  left: 0;
}

@media (max-aspect-ratio: 4/3) {
  .speed-selector {
    position: static;
  }
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
