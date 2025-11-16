<template>
  <div class="podcast-source-wrapper">
    <div class="podcast-container">

      <!-- Header: Search mode -->
      <ModalHeader :title="viewMode === 'search' ? 'Search Podcasts' : viewMode === 'subscriptions' ? 'Subscriptions' : viewMode === 'favorites' ? 'Favorites' : 'Podcast'" variant="neutral" icon="podcast">
        <template #actions>
          <CircularIcon v-if="viewMode !== 'search'" icon="search" variant="light" @click="viewMode = 'search'" />
          <CircularIcon v-if="viewMode !== 'subscriptions'" icon="list" variant="light" @click="loadSubscriptionsView" />
          <CircularIcon v-if="viewMode !== 'favorites'" icon="heart" variant="light" @click="loadFavoritesView" />
        </template>
      </ModalHeader>

      <!-- Search Section -->
      <div v-if="viewMode === 'search'" class="search-section">
        <div class="filters">
          <InputText v-model="podcastStore.searchQuery" placeholder="Search podcasts or episodes..."
            input-class="text-body-small" icon="search" :icon-size="24" @update:modelValue="handleSearch" />

          <div class="search-type-toggle">
            <Button :variant="searchType === 'podcasts' ? 'toggle' : 'ghost'" :active="searchType === 'podcasts'"
              @click="setSearchType('podcasts')">
              Podcasts
            </Button>
            <Button :variant="searchType === 'episodes' ? 'toggle' : 'ghost'" :active="searchType === 'episodes'"
              @click="setSearchType('episodes')">
              Episodes
            </Button>
          </div>

          <!-- Filter Grid -->
          <div class="filter-grid">
            <Dropdown
              v-model="podcastStore.countryFilter"
              :options="countryOptions"
              size="small"
              @change="handleFilterChange" />

            <Dropdown
              v-model="podcastStore.genreFilter"
              :options="genreOptions"
              size="small"
              @change="handleFilterChange" />

            <Dropdown
              v-model="podcastStore.languageFilter"
              :options="languageOptions"
              size="small"
              @change="handleFilterChange" />

            <Dropdown
              v-model="podcastStore.sortBy"
              :options="sortOptions"
              size="small"
              @change="handleFilterChange" />
          </div>
        </div>
      </div>

      <!-- Content Area -->
      <div class="content-area">

        <!-- Loading state -->
        <div v-if="podcastStore.loading" class="message-wrapper">
          <div class="message-content">
            <Icon name="podcast" :size="96" color="var(--color-background-glass)" />
            <p class="text-mono">Loading...</p>
          </div>
        </div>

        <!-- Error state -->
        <div v-else-if="podcastStore.hasError" class="message-wrapper">
          <div class="message-content">
            <Icon name="stop" :size="96" color="var(--color-background-glass)" />
            <p class="text-mono">Connection Error</p>
            <Button variant="toggle" :active="false" @click="retrySearch">
              Retry
            </Button>
          </div>
        </div>

        <!-- Empty state -->
        <div v-else-if="displayItems.length === 0" class="message-wrapper">
          <div class="message-content">
            <Icon name="podcast" :size="96" color="var(--color-background-glass)" />
            <p class="text-mono">{{ emptyMessage }}</p>
          </div>
        </div>

        <!-- Search Results: Podcasts -->
        <div v-else-if="viewMode === 'search' && searchType === 'podcasts'" class="results-grid">
          <div v-for="podcast in displayItems" :key="podcast.uuid" class="podcast-card"
            @click="viewPodcast(podcast.uuid)">
            <div class="podcast-image">
              <img v-if="podcast.image_url" :src="podcast.image_url" alt="" />
              <div v-else class="image-placeholder">
                <Icon name="podcast" :size="48" color="var(--color-text-light)" />
              </div>
            </div>
            <div class="podcast-info">
              <p class="podcast-title text-body">{{ podcast.name }}</p>
              <p class="podcast-publisher text-mono">{{ podcast.publisher }}</p>
            </div>
            <CircularIcon :icon="podcast.is_subscribed ? 'check' : 'plus'" variant="light"
              @click.stop="toggleSubscription(podcast)" />
          </div>

          <!-- Loading More Indicator -->
          <div v-if="podcastStore.isLoadingMore" class="loading-more">
            <Icon name="podcast" :size="48" color="var(--color-background-glass)" />
            <p class="text-mono">Chargement...</p>
          </div>

          <!-- Scroll Sentinel -->
          <div ref="scrollSentinel" class="scroll-sentinel"></div>
        </div>

        <!-- Search Results: Episodes OR Favorites OR Subscription Episodes -->
        <div v-else-if="(viewMode === 'search' && searchType === 'episodes') || viewMode === 'favorites' || viewMode === 'podcast'" class="episodes-list">
          <EpisodeCard v-for="episode in displayItems" :key="episode.uuid" :episode="episode" variant="card"
            :is-active="podcastStore.currentEpisode?.uuid === episode.uuid"
            :is-playing="podcastStore.currentEpisode?.uuid === episode.uuid && podcastStore.isPlaying"
            :is-loading="podcastStore.currentEpisode?.uuid === episode.uuid && podcastStore.isBuffering"
            @click="playEpisode(episode.uuid)" @play="playEpisode(episode.uuid)" />

          <!-- Load More button for podcast view -->
          <Button v-if="viewMode === 'podcast' && podcastStore.hasMoreEpisodes" variant="ghost"
            @click="podcastStore.loadMoreEpisodes()" :disabled="podcastStore.loadingEpisodes">
            {{ podcastStore.loadingEpisodes ? 'Loading...' : 'Load More Episodes' }}
          </Button>

          <!-- Loading More Indicator for search -->
          <div v-if="viewMode === 'search' && podcastStore.isLoadingMore" class="loading-more">
            <Icon name="podcast" :size="48" color="var(--color-background-glass)" />
            <p class="text-mono">Chargement...</p>
          </div>

          <!-- Scroll Sentinel for search -->
          <div v-if="viewMode === 'search'" ref="scrollSentinel" class="scroll-sentinel"></div>
        </div>

        <!-- Subscriptions View -->
        <div v-else-if="viewMode === 'subscriptions'" class="subscriptions-grid">
          <div v-for="podcast in displayItems" :key="podcast.uuid" class="podcast-card"
            @click="viewPodcast(podcast.uuid)">
            <div class="podcast-image">
              <img v-if="podcast.image_url" :src="podcast.image_url" alt="" />
              <div v-else class="image-placeholder">
                <Icon name="podcast" :size="48" color="var(--color-text-light)" />
              </div>
            </div>
            <div class="podcast-info">
              <p class="podcast-title text-body">{{ podcast.name }}</p>
              <p class="podcast-publisher text-mono">{{ podcast.total_episodes }} episodes</p>
            </div>
          </div>
        </div>

      </div>
    </div>

    <!-- Now Playing Panel -->
    <div v-if="podcastStore.currentEpisode" class="now-playing-wrapper">
      <EpisodeCard :episode="podcastStore.currentEpisode" variant="now-playing" :is-playing="podcastStore.isPlaying"
        :position="podcastStore.currentPosition" :duration="podcastStore.currentDuration"
        @play="togglePlayPause" @favorite="toggleFavorite" @seek="seekTo" />
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue';
import { usePodcastStore } from '@/stores/podcastStore';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import useWebSocket from '@/services/websocket';
import ModalHeader from '@/components/ui/ModalHeader.vue';
import CircularIcon from '@/components/ui/CircularIcon.vue';
import InputText from '@/components/ui/InputText.vue';
import Button from '@/components/ui/Button.vue';
import Icon from '@/components/ui/Icon.vue';
import Dropdown from '@/components/ui/Dropdown.vue';
import EpisodeCard from '@/components/audio/EpisodeCard.vue';
import { createPodcastCountryOptions } from '@/constants/podcast_countries';
import { createPodcastGenreOptions } from '@/constants/podcast_genres';
import { createPodcastLanguageOptions } from '@/constants/podcast_languages';

const podcastStore = usePodcastStore();
const unifiedStore = useUnifiedAudioStore();
const { on } = useWebSocket();

const viewMode = ref('search'); // 'search', 'podcast', 'subscriptions', 'favorites'
const searchType = ref('podcasts'); // 'podcasts' or 'episodes'
const searchDebounceTimer = ref(null);
const scrollSentinel = ref(null);

// Dropdown options
const countryOptions = computed(() => createPodcastCountryOptions('Tous les pays'));
const genreOptions = computed(() => createPodcastGenreOptions('Tous les genres'));
const languageOptions = computed(() => createPodcastLanguageOptions('Toutes les langues'));
const sortOptions = computed(() => [
  { label: 'Plus pertinent', value: 'EXACTNESS' },
  { label: 'Plus populaire', value: 'POPULARITY' }
]);

// Display items based on current view
const displayItems = computed(() => {
  if (viewMode.value === 'search') {
    return podcastStore.searchResults || [];
  } else if (viewMode.value === 'subscriptions') {
    return podcastStore.subscriptions || [];
  } else if (viewMode.value === 'favorites') {
    return podcastStore.favorites || [];
  } else if (viewMode.value === 'podcast') {
    return podcastStore.selectedPodcastEpisodes || [];
  }
  return [];
});

const emptyMessage = computed(() => {
  if (viewMode.value === 'search') {
    return podcastStore.searchQuery ? 'No results found' : 'Search for podcasts or episodes';
  } else if (viewMode.value === 'subscriptions') {
    return 'No subscriptions yet';
  } else if (viewMode.value === 'favorites') {
    return 'No favorite episodes yet';
  } else if (viewMode.value === 'podcast') {
    return 'No episodes available';
  }
  return 'No content';
});

// Handle search with debounce
function handleSearch() {
  if (searchDebounceTimer.value) {
    clearTimeout(searchDebounceTimer.value);
  }

  // Reset pagination for new search
  podcastStore.currentPage = 1;
  podcastStore.hasMoreResults = true;

  searchDebounceTimer.value = setTimeout(async () => {
    if (podcastStore.searchQuery.trim()) {
      await podcastStore.search(podcastStore.searchQuery, searchType.value);
    } else {
      podcastStore.searchResults = [];
    }
  }, 500);
}

// Handle filter change
function handleFilterChange() {
  // Reset pagination
  podcastStore.currentPage = 1;
  podcastStore.hasMoreResults = true;

  // Trigger search if there's a query
  if (podcastStore.searchQuery.trim()) {
    handleSearch();
  }
}

function setSearchType(type) {
  searchType.value = type;
  if (podcastStore.searchQuery.trim()) {
    handleSearch();
  }
}

function retrySearch() {
  handleSearch();
}

async function viewPodcast(podcastUuid) {
  await podcastStore.getPodcastSeries(podcastUuid, 1);
  viewMode.value = 'podcast';
}

async function loadSubscriptionsView() {
  await podcastStore.loadSubscriptions();
  viewMode.value = 'subscriptions';
}

async function loadFavoritesView() {
  await podcastStore.loadFavorites();
  viewMode.value = 'favorites';
}

async function playEpisode(episodeUuid) {
  await podcastStore.playEpisode(episodeUuid);
}

async function togglePlayPause() {
  if (podcastStore.isPlaying) {
    await podcastStore.pause();
  } else {
    await podcastStore.resume();
  }
}

async function toggleFavorite() {
  if (!podcastStore.currentEpisode) return;

  if (podcastStore.isFavorite(podcastStore.currentEpisode.uuid)) {
    await podcastStore.removeFavorite(podcastStore.currentEpisode.uuid);
  } else {
    await podcastStore.addFavorite(podcastStore.currentEpisode.uuid);
  }
}

async function toggleSubscription(podcast) {
  if (podcast.is_subscribed) {
    await podcastStore.unsubscribe(podcast.uuid);
  } else {
    await podcastStore.subscribe(podcast.uuid);
  }
  // Refresh search results
  if (podcastStore.searchQuery.trim()) {
    await podcastStore.search(podcastStore.searchQuery, searchType.value);
  }
}

async function seekTo(position) {
  await podcastStore.seek(position);
}

// Listen to WebSocket events
onMounted(() => {
  on('state_changed', (state) => {
    podcastStore.handleStateUpdate(state);
  });

  // Refresh status on mount
  podcastStore.refreshStatus();

  // Setup infinite scroll observer
  if (scrollSentinel.value) {
    const observer = new IntersectionObserver(
      (entries) => {
        const entry = entries[0];
        if (entry.isIntersecting && viewMode.value === 'search' && podcastStore.searchQuery.trim()) {
          // Load more results when sentinel becomes visible
          podcastStore.loadMoreResults();
        }
      },
      {
        root: null, // Use viewport as root
        rootMargin: '100px', // Trigger 100px before reaching sentinel
        threshold: 0.1
      }
    );

    observer.observe(scrollSentinel.value);

    // Cleanup on unmount
    return () => {
      observer.disconnect();
    };
  }
});
</script>

<style scoped>
.podcast-source-wrapper {
  display: flex;
  gap: var(--space-06);
  height: 100%;
  padding: var(--space-06);
}

.podcast-container {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: var(--space-05);
  overflow: hidden;
}

.search-section {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

.filters {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

.search-type-toggle {
  display: flex;
  gap: var(--space-02);
}

.filter-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: var(--space-02);
}

.content-area {
  flex: 1;
  overflow-y: auto;
}

.message-wrapper {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  min-height: 300px;
}

.message-content {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-04);
  text-align: center;
}

/* Podcast Grid (search results and subscriptions) */
.results-grid,
.subscriptions-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: var(--space-04);
  padding: var(--space-02);
}

.podcast-card {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
  padding: var(--space-04);
  background: var(--color-background-neutral);
  border-radius: var(--radius-05);
  cursor: pointer;
  transition: all var(--transition-fast);
  border: 1px solid var(--color-border);
}

.podcast-card:hover {
  background: var(--color-background-strong);
  transform: translateY(-2px);
  box-shadow: var(--shadow-02);
}

.podcast-image {
  width: 100%;
  aspect-ratio: 1;
  border-radius: var(--radius-03);
  overflow: hidden;
  background: var(--color-background-glass);
}

.podcast-image img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.image-placeholder {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
}

.podcast-info {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
}

.podcast-title {
  font-weight: 500;
  color: var(--color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.podcast-publisher {
  color: var(--color-text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* Episodes List */
.episodes-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
  padding: var(--space-02);
}

/* Loading More Indicator */
.loading-more {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-02);
  padding: var(--space-05);
  grid-column: 1 / -1; /* Span all columns in grid */
}

/* Scroll Sentinel (invisible trigger for infinite scroll) */
.scroll-sentinel {
  height: 1px;
  grid-column: 1 / -1; /* Span all columns in grid */
}

/* Now Playing Panel */
.now-playing-wrapper {
  width: 400px;
  flex-shrink: 0;
  overflow-y: auto;
}

/* Mobile Layout */
@media (max-aspect-ratio: 4/3) {
  .podcast-source-wrapper {
    flex-direction: column;
    padding: var(--space-04);
  }

  .now-playing-wrapper {
    width: 100%;
    position: sticky;
    bottom: 0;
    background: var(--color-background);
    padding-top: var(--space-04);
    border-top: 1px solid var(--color-border);
  }

  .results-grid,
  .subscriptions-grid {
    grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  }
}
</style>
