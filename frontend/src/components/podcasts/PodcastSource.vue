<template>
  <AudioSourceLayout ref="audioLayoutRef" :show-player="shouldShowPlayerLayout"
    :header-title="currentTitle"
    :header-subtitle="currentSubtitle"
    :header-show-back="canGoBack"
    :header-title-muted="currentView === 'podcast-details' || currentView === 'episode-details'"
    header-icon="podcast" header-variant="background-neutral"
    :header-actions-key="currentView" :content-key="currentView"
    :player-mobile-height="144" :pending-scroll-restore="pendingScrollRestore" gradient="podcast" @header-back="goBack"
    @scroll-restored="onScrollRestored">
    <!-- Header actions (only on home view) -->
    <template v-if="currentView === 'home'" #header-actions="{ iconVariant }">
      <IconButton icon="heartOff" :variant="iconVariant" @click="goToSubscriptions" />
      <IconButton icon="queue" :variant="iconVariant" @click="goToQueue" />
      <IconButton icon="search" :variant="iconVariant" @click="goToSearch" />
    </template>

    <!-- Content slot: scrollable views -->
    <template #content>
        <!-- Home View (Discovery) -->
        <HomeView v-if="currentView === 'home'" key="home" @select-podcast="openPodcastDetails"
          @select-episode="openEpisodeDetails" @play-episode="playEpisode" @browse-genre="goToGenre" />

        <!-- Subscriptions View -->
        <SubscriptionsView v-else-if="currentView === 'subscriptions'" key="subscriptions"
          @select-podcast="openPodcastDetails" @select-episode="openEpisodeDetails" @play-episode="playEpisode" />

        <!-- Search View -->
        <SearchView v-else-if="currentView === 'search'" key="search" @select-podcast="openPodcastDetails" />

        <!-- Queue View -->
        <QueueView v-else-if="currentView === 'queue'" key="queue" @select-episode="openEpisodeDetails"
          @play-episode="playEpisode" @select-podcast="openPodcastDetails" />

        <!-- Genre View -->
        <GenreView v-else-if="currentView === 'genre'" key="genre" :genre="selectedGenre"
          :genreLabel="selectedGenreLabel" @select-podcast="openPodcastDetails"
          @select-episode="openEpisodeDetails" @play-episode="playEpisode" />

        <!-- Podcast Details (full screen overlay) -->
        <PodcastDetails v-else-if="currentView === 'podcast-details'" key="podcast-details" :uuid="selectedPodcastUuid"
          @play-episode="playEpisode" @select-episode="openEpisodeDetails" @unavailable="onPodcastUnavailable" />

        <!-- Episode Details (full screen overlay) -->
        <EpisodeDetails v-else-if="currentView === 'episode-details'" key="episode-details" :uuid="selectedEpisodeUuid"
          @play-episode="playEpisode" @select-podcast="openPodcastDetails" />
    </template>

    <!-- Player slot: AudioPlayer component -->
    <template #player>
      <AudioPlayer :visible="shouldShowPlayerLayout" source="podcast" :artwork="episodeImage" :title="episodeName"
        @after-hide="onAfterHide"
        :is-playing="isCurrentlyPlaying" :is-loading="isBuffering" :swipe-enabled="canSkip"
        @swipe-next="seekForward" @swipe-prev="seekBackward">
        <!-- Track info: podcast name kicker + episode title, in the shared
             PlayerInfoText's vertical layout (desktop sidebar and, since nothing
             hides it there, the mobile expanded sheet too); the mobile mini-bar's
             compact single-line horizontal layout renders its own title/podcast-name
             pair instead. That pair is only ever relevant to the mobile docked
             mini-bar (CSS never shows .horizontal-layout inside the expanded card),
             so `expanded` skips rendering it there entirely instead of emitting
             always-hidden markup. -->
        <template #info="{ expanded }">
          <PlayerInfoText class="vertical-layout" :kicker="podcastName" :title="episodeName" />
          <template v-if="!expanded">
            <p class="player-title text-body horizontal-layout">{{ episodeName }}</p>
            <p v-if="podcastName" class="player-subtitle text-body horizontal-layout">{{ podcastName }}</p>
          </template>
        </template>

        <!-- Progress bar: once the episode has a duration and a playhead;
             seekable while the source takes `seek` -->
        <template #progress>
          <div v-if="showProgress" @click.stop>
            <ProgressBar :currentPosition="positionMs" :duration="durationMs"
              :progressPercentage="livePercent" :interactive="canSeek" variant="dark" @seek="seekTo" />
          </div>
        </template>

        <!-- Podcast controls: play/pause everywhere; seek buttons + speed selector are
             desktop-only — on mobile the mini-player's swipe gesture covers +30s (right)
             / -15s (left), speed moves into the future expanded mini-player view. -->
        <template #controls>
          <!-- The seek pair takes `secondary-round`, not `secondary`: it fills
               its box in both axes, and on `secondary` — a rung calibrated on
               the flattest glyph there is — it came out larger than the pause
               it flanks. A rung below would leave its two digits unreadable,
               which is the floor that sets the value. The measurements are in
               design-system.css. `desktop-only` hides the pair in the docked
               bar but not in the expanded sheet, which is where a phone
               actually sees it. -->
          <div class="playback-controls" @click.stop>
            <IconButton v-if="canSkip" icon="rewind15" variant="ghost" size="small"
              class="desktop-only transport-secondary-round" @click="seekBackward" />

            <IconButton :icon="pausesOnPress ? 'pause' : 'play'" variant="ghost" size="medium"
              class="transport-primary" :loading="isBuffering" @click="togglePlayPause" />

            <IconButton v-if="canSkip" icon="forward30" variant="ghost" size="small"
              class="desktop-only transport-secondary-round" @click="seekForward" />
          </div>

          <div v-if="controls.includes('set_speed')" class="speed-selector desktop-only" @click.stop>
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
import { useI18n } from '@/services/i18n'
import { apiCall } from '@/services/apiCall'
import { logger } from '@/services/logger'
import IconButton from '@/components/ui/IconButton.vue'
import AudioPlayer from '@/components/audio/AudioPlayer.vue'
import AudioSourceLayout from '@/components/audio/AudioSourceLayout.vue'
import PlayerInfoText from '@/components/audio/PlayerInfoText.vue'
import Dropdown from '@/components/ui/Dropdown.vue'

// Views
import HomeView from './HomeView.vue'
import SubscriptionsView from './SubscriptionsView.vue'
import SearchView from './SearchView.vue'
import QueueView from './QueueView.vue'
import GenreView from './GenreView.vue'
import PodcastDetails from './PodcastDetails.vue'
import EpisodeDetails from './EpisodeDetails.vue'
import ProgressBar from '@/components/audio/ProgressBar.vue'
import { podcastPlaceholder } from '@/constants/placeholders'

const podcastStore = usePodcastStore()
const unifiedStore = useUnifiedAudioStore()
const { t } = useI18n()

// Ref to AudioSourceLayout — used to access its scroll container for position save/restore
const audioLayoutRef = ref(null)
const layoutScrollRef = computed(() => audioLayoutRef.value?.scrollElement ?? null)

// Navigation with stack — scrollElRef enables scroll position save on push() and restore on back()
const { currentView, currentParams, canGoBack, push, back, pendingScrollRestore } =
  useNavigationStack('home', { scrollElRef: layoutScrollRef })

// The pane follows the episode, and the episode survives a stop: an auto-stop
// publishes the one a play press would reopen, at the second it stopped. An
// ending publishes no resume identity, so the player goes with it. The store's
// sticky displayEpisode was a copy of that fact for the length of a fade.
const {
  isPlaying: isCurrentlyPlaying, isBuffering,
  shouldShowPlayer: shouldShowPlayerLayout,
  displayed: episode, onAfterHide
} = useSourcePlaybackVisibility('podcast', {
  content: () => podcastStore.currentEpisode
})

// The playhead (ms), from the session's position anchor — or the resume point
// while no session runs.
const {
  currentPosition: positionMs,
  duration: durationMs,
  progressPercentage: livePercent,
  seekTo,
  skip,
  isPositionInitialized,
} = useSourceProgress('podcast')

// What the source takes right now, and the phase it answers from.
const controls = computed(() =>
  unifiedStore.systemState.source === 'podcast' ? unifiedStore.systemState.controls : []
)
const phase = computed(() =>
  unifiedStore.systemState.source === 'podcast' ? unifiedStore.systemState.session?.phase : null
)
const canSeek = computed(() => controls.value.includes('seek'))
const canSkip = computed(() => controls.value.includes('skip'))
const pausesOnPress = computed(() => phase.value === 'playing' || phase.value === 'loading')
const showProgress = computed(() => durationMs.value > 0 && isPositionInitialized.value)

// Navigation params (stored separately since composable handles view state)
const selectedPodcastUuid = computed(() => currentParams.value.podcastUuid || '')
const selectedPodcastName = computed(() => currentParams.value.podcastName || '')
const selectedEpisodeUuid = computed(() => currentParams.value.episodeUuid || '')
const selectedGenre = computed(() => currentParams.value.genre || '')
const selectedGenreLabel = computed(() => currentParams.value.genreLabel || '')

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

function openPodcastDetails(podcastOrUuid) {
  // A chart, search or subscription entry carries its own uuid (the Apple id),
  // so opening one is a navigation and nothing else — there is no resolution
  // step left that could fail between seeing a podcast and opening it.
  if (typeof podcastOrUuid === 'string') {
    push('podcast-details', { podcastUuid: podcastOrUuid })
    return
  }
  if (!podcastOrUuid?.uuid) {
    logger.error('podcast', 'Invalid podcast data', podcastOrUuid)
    return
  }
  // The name travels so the "not available" notice can name the podcast when
  // the details view finds no public feed for it.
  push('podcast-details', {
    podcastUuid: podcastOrUuid.uuid,
    podcastName: podcastOrUuid.name || '',
  })
}

// Raised by PodcastDetails when the series could not be loaded. `reason` keeps
// a permanent absence apart from a passing failure — the same distinction the
// backend draws between 404 and 503.
function onPodcastUnavailable(reason) {
  // The fetch is async: the owner may have pressed back before it answered, and
  // popping again would leave the genre view for the home screen and show a
  // notice naming nothing.
  if (currentView.value !== 'podcast-details') return

  unifiedStore.transientNotice = reason === 'transient'
    ? { title: t('podcasts.catalogUnavailable'), detail: t('podcasts.catalogUnavailableHint') }
    : { title: t('podcasts.notAvailable'), detail: selectedPodcastName.value || null }
  back()
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

// Episode artwork — the backend keeps the episode through a stop
const episodeImage = computed(() => {
  return episode.value?.image_url || podcastPlaceholder
})

// Episode name — same source of truth, no local copy
const episodeName = computed(() => {
  return episode.value?.name || t('podcasts.noEpisode')
})

// Podcast name — same source of truth, no local copy
const podcastName = computed(() => {
  return episode.value?.podcast?.name || ''
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
  if (pausesOnPress.value) {
    if (controls.value.includes('pause')) await podcastStore.pause()
  } else if (controls.value.includes('resume')) {
    await podcastStore.resume()
  }
}

// −15 / +30 are relative: the source adds them to where its playhead is, so
// quick presses add up (useSourceProgress.skip shows the sum at once).
async function seekBackward() {
  if (!canSkip.value) return
  await skip(-15)
}

async function seekForward() {
  if (!canSkip.value) return
  await skip(30)
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

/* The .speed-selector layout lives in AudioPlayer.vue, in :deep() — it is
   slotted into it, and the same row is re-authored by the gallery's
   SourceStage, which scoped CSS here could never reach. */
</style>
