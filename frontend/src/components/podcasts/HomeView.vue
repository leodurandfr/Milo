<template>
  <div class="home-view">
    <!-- New episodes from subscriptions (Bloc 1) - Only show if user has subscriptions -->
    <section v-if="hasSubscriptions" class="section">
      <h2 class="section-title heading-2">{{ t('podcasts.newEpisodesFromSubscriptions') }}</h2>
      <div class="transition-container">
        <!-- Show skeletons while loading -->
        <transition name="content-fade">
          <div v-if="loadingSubscriptions" key="loading-sub" class="episodes-list">
            <SkeletonEpisodeCard v-for="i in 4" :key="`skeleton-sub-${i}`" />
          </div>
        </transition>

        <!-- Real cards when loaded -->
        <transition name="content-fade">
          <div v-if="!loadingSubscriptions && latestSubscriptionEpisodes.length > 0" key="loaded-sub" class="episodes-list">
            <EpisodeCard
              v-for="episode in latestSubscriptionEpisodes.slice(0, 4)"
              :key="episode.uuid"
              :episode="episode"
              @select="$emit('select-episode', episode.uuid)"
              @play="$emit('play-episode', episode)"
              @pause="handlePause"
            />
          </div>
        </transition>

        <!-- Empty state with MessageContent when subscribed but no new episodes -->
        <transition name="content-fade">
          <div v-if="!loadingSubscriptions && latestSubscriptionEpisodes.length === 0" key="empty-sub" class="message-wrapper">
            <MessageContent>
              <SvgIcon name="heartOff" :size="64" color="var(--color-background-medium-16)" />
              <p class="heading-2">{{ t('podcasts.noNewEpisodes') }}</p>
            </MessageContent>
          </div>
        </transition>
      </div>
    </section>

    <!-- Top Podcasts (Bloc 2) -->
    <section class="section">
      <h2 class="section-title heading-2">{{ t('podcasts.topPodcasts') }}</h2>
      <div class="transition-container">
        <!-- Show skeletons while loading -->
        <transition name="content-fade">
          <div v-if="loadingTopCharts" key="loading-podcasts" class="podcasts-grid">
            <SkeletonPodcastCard v-for="i in 6" :key="`skeleton-podcast-${i}`" />
          </div>
        </transition>

        <!-- Real cards when loaded -->
        <transition name="content-fade">
          <div v-if="!loadingTopCharts" key="loaded-podcasts" class="podcasts-grid">
            <PodcastCard
              v-for="(podcast, index) in topCharts.slice(0, 6)"
              :key="podcast.uuid"
              :podcast="podcast"
              :position="index + 1"
              @select="$emit('select-podcast', podcast.uuid)"
            />
          </div>
        </transition>
      </div>
    </section>

    <!-- Browse by Genre (Bloc 3) -->
    <section class="section">
      <h2 class="section-title heading-2">{{ t('podcasts.browseByGenre') }}</h2>
      <div class="genres-grid">
        <GenreCard
          v-for="genre in mainGenres"
          :key="genre.value"
          :value="genre.value"
          :label="genre.label"
          @click="browseGenre(genre.value)"
        />
      </div>
    </section>

    <!-- Top Episodes (Bloc 4) -->
    <section class="section">
      <h2 class="section-title heading-2">{{ t('podcasts.topEpisodes') }}</h2>
      <div class="transition-container">
        <!-- Show skeletons while loading -->
        <transition name="content-fade">
          <div v-if="loadingTopEpisodes" key="loading-episodes" class="episodes-list">
            <SkeletonEpisodeCard v-for="i in 6" :key="`skeleton-episode-${i}`" />
          </div>
        </transition>

        <!-- Real cards when loaded -->
        <transition name="content-fade">
          <div v-if="!loadingTopEpisodes" key="loaded-episodes" class="episodes-list">
            <EpisodeCard
              v-for="episode in topEpisodes.slice(0, 6)"
              :key="episode.uuid"
              :episode="episode"
              @select="$emit('select-episode', episode.uuid)"
              @play="$emit('play-episode', episode)"
              @pause="handlePause"
            />
          </div>
        </transition>
      </div>
    </section>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { usePodcastStore } from '@/stores/podcastStore'
import { useI18n } from '@/services/i18n'
import PodcastCard from './PodcastCard.vue'
import EpisodeCard from './EpisodeCard.vue'
import GenreCard from './GenreCard.vue'
import SkeletonPodcastCard from './SkeletonPodcastCard.vue'
import SkeletonEpisodeCard from './SkeletonEpisodeCard.vue'
import MessageContent from '@/components/ui/MessageContent.vue'
import SvgIcon from '@/components/ui/SvgIcon.vue'

const emit = defineEmits(['select-podcast', 'select-episode', 'play-episode', 'browse-genre'])
const { t } = useI18n()

const podcastStore = usePodcastStore()

const loadingTopCharts = ref(true)
const loadingTopEpisodes = ref(true)
const loadingSubscriptions = ref(true)
const topCharts = ref([])
const topEpisodes = ref([])

// Use store's computed for hasSubscriptions (preloaded in App.vue)
const hasSubscriptions = computed(() => podcastStore.hasSubscriptions)
const latestSubscriptionEpisodes = computed(() => podcastStore.latestSubscriptionEpisodes)

const mainGenres = computed(() => [
  { value: 'PODCASTSERIES_COMEDY', label: t('podcasts.genres.comedy') },
  { value: 'PODCASTSERIES_SOCIETY_AND_CULTURE', label: t('podcasts.genres.society_and_culture') },
  { value: 'PODCASTSERIES_NEWS', label: t('podcasts.genres.news') },
  { value: 'PODCASTSERIES_TRUE_CRIME', label: t('podcasts.genres.trueCrime') },
  { value: 'PODCASTSERIES_BUSINESS', label: t('podcasts.genres.business') },
  { value: 'PODCASTSERIES_EDUCATION', label: t('podcasts.genres.education') },
  { value: 'PODCASTSERIES_HEALTH_AND_FITNESS', label: t('podcasts.genres.health') },
  { value: 'PODCASTSERIES_SPORTS', label: t('podcasts.genres.sports') },
  { value: 'PODCASTSERIES_ARTS', label: t('podcasts.genres.arts') },
  { value: 'PODCASTSERIES_SCIENCE', label: t('podcasts.genres.science') },
  { value: 'PODCASTSERIES_TV_AND_FILM', label: t('podcasts.genres.tv_and_film') },
  { value: 'PODCASTSERIES_MUSIC', label: t('podcasts.genres.music') }
])

function browseGenre(genreValue) {
  // Find genre label
  const genre = mainGenres.value.find(g => g.value === genreValue)
  if (genre) {
    emit('browse-genre', genreValue, genre.label)
  }
}

async function handlePause() {
  await podcastStore.pause()
}

async function loadData() {
  // Note: Country/language is automatically derived from user settings on the backend

  // Load subscriptions via store (cached, preloaded in App.vue)
  loadingSubscriptions.value = true
  try {
    await podcastStore.loadSubscriptions()
  } catch (error) {
    console.error('Error loading subscription episodes:', error)
  } finally {
    loadingSubscriptions.value = false
  }

  // Load top podcasts (Bloc 2) - backend derives country from user's language setting
  loadingTopCharts.value = true
  try {
    const response = await fetch('/api/podcast/discover/top-charts?content_type=PODCASTSERIES&limit=10')
    const data = await response.json()
    topCharts.value = data.results || []
  } catch (error) {
    console.error('Error loading top charts:', error)
  } finally {
    loadingTopCharts.value = false
  }

  // Load top episodes (Bloc 4) - backend derives country from user's language setting
  loadingTopEpisodes.value = true
  try {
    const response = await fetch('/api/podcast/discover/top-charts?content_type=PODCASTEPISODE&limit=10')
    const data = await response.json()
    topEpisodes.value = podcastStore.enrichEpisodesWithProgress(data.results || [])
  } catch (error) {
    console.error('Error loading top episodes:', error)
  } finally {
    loadingTopEpisodes.value = false
  }
}

onMounted(() => {
  loadData()
})
</script>

<style scoped>
.home-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-07);
}

.section {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

.section-title {
  color: var(--color-text);
  margin: 0;
}

/* Transition container for overlay effect */
.transition-container {
  display: grid;
  grid-template-columns: 1fr;
}

.transition-container > * {
  grid-column: 1;
  grid-row: 1;
}

.podcasts-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--space-02);
}

.episodes-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.message-wrapper {
  background: var(--color-background-neutral);
  border-radius: var(--radius-04);
}

.genres-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--space-02);
}

/* Content fade transition (skeleton to real content) */
.content-fade-enter-active,
.content-fade-leave-active {
  transition: opacity var(--transition-normal);
}

.content-fade-enter-from,
.content-fade-leave-to {
  opacity: 0;
}

/* Card fade-in transition */
.fade-in-cards-enter-active {
  transition: opacity var(--transition-normal), transform var(--transition-normal);
}

.fade-in-cards-enter-from {
  opacity: 0;
  transform: translateY(8px);
}

.fade-in-cards-enter-to {
  opacity: 1;
  transform: translateY(0);
}

/* Empty state message */
.empty-message {
  color: var(--color-text-secondary);
  text-align: center;
  padding: var(--space-07) var(--space-05);
  margin: 0;
}

/* Mobile: Responsive adaptations */
@media (max-aspect-ratio: 4/3) {
  .podcasts-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}
</style>
