<template>
  <!-- The navigation, and the full player it expands into: BrowserSourceViews
       swaps the two. -->
  <BrowserSourceViews source="podcast" :playback="playback">
    <template #navigation="{ bar }">
      <AudioSourceLayout ref="audioLayoutRef" :show-player="shouldShowPlayerLayout"
        :header-title="currentTitle"
        :header-subtitle="currentSubtitle"
        :header-show-back="canGoBack"
        :header-title-muted="currentView === 'podcast-details' || currentView === 'episode-details'"
        header-icon="podcast"
        :header-actions-key="currentView" :content-key="currentView"
        :player-mobile-height="144" :pending-scroll-restore="pendingScrollRestore" gradient="podcast" @header-back="goBack"
        @scroll-restored="onScrollRestored">
        <!-- Header actions (only on home view) -->
        <template v-if="currentView === 'home'" #header-actions>
          <IconButton icon="heartOff" @click="goToSubscriptions" />
          <IconButton icon="queue" @click="goToQueue" />
          <IconButton icon="search" @click="goToSearch" />
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
              @select-podcast="openPodcastDetails" />

            <!-- Podcast Details (full screen overlay) -->
            <PodcastDetails v-else-if="currentView === 'podcast-details'" key="podcast-details" :uuid="selectedPodcastUuid"
              @play-episode="playEpisode" @select-episode="openEpisodeDetails" @unavailable="onPodcastUnavailable" />

            <!-- Episode Details (full screen overlay) -->
            <EpisodeDetails v-else-if="currentView === 'episode-details'" key="episode-details" :uuid="selectedEpisodeUuid"
              @play-episode="playEpisode" @select-podcast="openPodcastDetails" />
        </template>

        <!-- The playing bar reads what it draws from the state. -->
        <template #player>
          <AudioPlayer v-bind="bar" source="podcast" />
        </template>
      </AudioSourceLayout>
    </template>
  </BrowserSourceViews>
</template>

<script setup>
import { ref, computed, onBeforeUnmount, watch } from 'vue'
import { usePodcastStore } from '@/stores/podcastStore'
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore'
import { useNavigationStack } from '@/composables/useNavigationStack'
import { useSourcePlaybackVisibility } from '@/composables/useSourcePlaybackVisibility'
import { useI18n } from '@/services/i18n'
import { logger } from '@/services/logger'
import IconButton from '@/components/ui/IconButton.vue'
import AudioPlayer from '@/components/audio/AudioPlayer.vue'
import BrowserSourceViews from '@/components/audio/BrowserSourceViews.vue'
import AudioSourceLayout from '@/components/audio/AudioSourceLayout.vue'

// Views
import HomeView from './HomeView.vue'
import SubscriptionsView from './SubscriptionsView.vue'
import SearchView from './SearchView.vue'
import QueueView from './QueueView.vue'
import GenreView from './GenreView.vue'
import PodcastDetails from './PodcastDetails.vue'
import EpisodeDetails from './EpisodeDetails.vue'

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
const playback = useSourcePlaybackVisibility('podcast', {
  content: () => podcastStore.currentEpisode
})
const { shouldShowPlayer: shouldShowPlayerLayout } = playback

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

onBeforeUnmount(() => {
  podcastStore.clearSearch()
})
</script>

<style scoped>
::-webkit-scrollbar {
  display: none;
}
</style>
