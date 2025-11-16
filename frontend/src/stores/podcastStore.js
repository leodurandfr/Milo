// frontend/src/stores/podcastStore.js
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import axios from 'axios';

export const usePodcastStore = defineStore('podcast', () => {
  // === STATE ===

  // Current playback
  const currentEpisode = ref(null);
  const isPlaying = ref(false);
  const isBuffering = ref(false);
  const currentPosition = ref(0);  // seconds
  const currentDuration = ref(0);  // seconds

  // Search and browse
  const searchQuery = ref('');
  const searchResults = ref([]);
  const searchType = ref('podcasts'); // 'podcasts' or 'episodes'
  const loading = ref(false);
  const hasError = ref(false);

  // Filters
  const countryFilter = ref('');
  const genreFilter = ref('');
  const languageFilter = ref('');
  const sortBy = ref('EXACTNESS');

  // Pagination
  const currentPage = ref(1);
  const hasMoreResults = ref(true);
  const isLoadingMore = ref(false);

  // Subscriptions
  const subscriptions = ref([]);
  const loadingSubscriptions = ref(false);

  // Favorites
  const favorites = ref([]);
  const loadingFavorites = ref(false);

  // Selected podcast (for viewing episodes)
  const selectedPodcast = ref(null);
  const selectedPodcastEpisodes = ref([]);
  const episodesPage = ref(1);
  const hasMoreEpisodes = ref(false);
  const loadingEpisodes = ref(false);

  // AbortController for canceling requests
  let currentAbortController = null;

  // === GETTERS ===

  const isSubscribed = computed(() => (podcastUuid) => {
    return subscriptions.value.some(s => s.uuid === podcastUuid);
  });

  const isFavorite = computed(() => (episodeUuid) => {
    return favorites.value.some(e => e.uuid === episodeUuid);
  });

  const playbackProgress = computed(() => {
    if (currentDuration.value === 0) return 0;
    return (currentPosition.value / currentDuration.value) * 100;
  });

  const formattedPosition = computed(() => {
    return formatTime(currentPosition.value);
  });

  const formattedDuration = computed(() => {
    return formatTime(currentDuration.value);
  });

  // === HELPER FUNCTIONS ===

  function formatTime(seconds) {
    if (!seconds || isNaN(seconds)) return '0:00';

    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const secs = Math.floor(seconds % 60);

    if (hours > 0) {
      return `${hours}:${minutes.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
    }
    return `${minutes}:${secs.toString().padStart(2, '0')}`;
  }

  // === ACTIONS ===

  /**
   * Search podcasts or episodes
   */
  async function search(query, type = 'podcasts') {
    // Cancel previous request
    if (currentAbortController) {
      currentAbortController.abort();
    }

    // If loading more, use isLoadingMore instead of loading
    if (currentPage.value > 1) {
      isLoadingMore.value = true;
    } else {
      loading.value = true;
      searchResults.value = []; // Clear results for new search
    }

    hasError.value = false;
    searchQuery.value = query;
    searchType.value = type;

    try {
      currentAbortController = new AbortController();

      const endpoint = type === 'podcasts' ? '/api/podcast/search/podcasts' : '/api/podcast/search/episodes';

      const response = await axios.get(endpoint, {
        params: {
          term: query,
          sort_by: sortBy.value,
          limit: 25,
          page: currentPage.value,
          country: countryFilter.value,
          genre: genreFilter.value,
          language: languageFilter.value
        },
        signal: currentAbortController.signal
      });

      const newResults = response.data.results || [];

      // Append or replace depending on page
      if (currentPage.value === 1) {
        searchResults.value = newResults;
      } else {
        searchResults.value.push(...newResults);
      }

      // Check if there are more results (if we got 25, there might be more)
      hasMoreResults.value = newResults.length === 25 && currentPage.value < 20;

      console.log(`✅ Found ${newResults.length} ${type} (page ${currentPage.value}, total ${searchResults.value.length})`);
      return true;

    } catch (error) {
      if (error.name === 'CanceledError') {
        console.log('🔄 Search request canceled');
        return false;
      }

      console.error('❌ Error searching:', error);
      hasError.value = true;
      if (currentPage.value === 1) {
        searchResults.value = [];
      }
      return false;

    } finally {
      loading.value = false;
      isLoadingMore.value = false;
      currentAbortController = null;
    }
  }

  /**
   * Get podcast series details with episodes
   */
  async function getPodcastSeries(podcastUuid, page = 1) {
    loadingEpisodes.value = true;
    hasError.value = false;

    try {
      const response = await axios.get(`/api/podcast/series/${podcastUuid}`, {
        params: { page, limit: 20 }
      });

      selectedPodcast.value = response.data;

      if (page === 1) {
        selectedPodcastEpisodes.value = response.data.episodes || [];
      } else {
        selectedPodcastEpisodes.value.push(...(response.data.episodes || []));
      }

      episodesPage.value = page;
      hasMoreEpisodes.value = selectedPodcastEpisodes.value.length < response.data.total_episodes;

      console.log(`✅ Loaded ${response.data.episodes?.length || 0} episodes (page ${page})`);
      return true;

    } catch (error) {
      console.error('❌ Error loading podcast series:', error);
      hasError.value = true;
      return false;

    } finally {
      loadingEpisodes.value = false;
    }
  }

  /**
   * Load more episodes for current podcast
   */
  async function loadMoreEpisodes() {
    if (!selectedPodcast.value || !hasMoreEpisodes.value || loadingEpisodes.value) {
      return false;
    }

    return await getPodcastSeries(selectedPodcast.value.uuid, episodesPage.value + 1);
  }

  /**
   * Play an episode
   */
  async function playEpisode(episodeUuid) {
    loading.value = true;
    hasError.value = false;

    try {
      const response = await axios.post('/api/podcast/play', {
        episode_uuid: episodeUuid
      });

      if (response.data.success) {
        // Get episode details
        const episodeResponse = await axios.get(`/api/podcast/episode/${episodeUuid}`);
        currentEpisode.value = episodeResponse.data;
        isBuffering.value = true;
        isPlaying.value = false;

        console.log(`✅ Playing episode: ${currentEpisode.value.name}`);
        return true;
      }

      return false;

    } catch (error) {
      console.error('❌ Error playing episode:', error);
      hasError.value = true;
      return false;

    } finally {
      loading.value = false;
    }
  }

  /**
   * Pause playback
   */
  async function pause() {
    try {
      const response = await axios.post('/api/podcast/pause');

      if (response.data.success) {
        isPlaying.value = false;
        console.log('⏸️ Paused');
        return true;
      }

      return false;

    } catch (error) {
      console.error('❌ Error pausing:', error);
      return false;
    }
  }

  /**
   * Resume playback
   */
  async function resume() {
    try {
      const response = await axios.post('/api/podcast/resume');

      if (response.data.success) {
        isPlaying.value = true;
        console.log('▶️ Resumed');
        return true;
      }

      return false;

    } catch (error) {
      console.error('❌ Error resuming:', error);
      return false;
    }
  }

  /**
   * Seek to position
   */
  async function seek(position) {
    try {
      const response = await axios.post('/api/podcast/seek', {
        position: Math.floor(position)
      });

      if (response.data.success) {
        currentPosition.value = position;
        console.log(`⏩ Seeked to ${formatTime(position)}`);
        return true;
      }

      return false;

    } catch (error) {
      console.error('❌ Error seeking:', error);
      return false;
    }
  }

  /**
   * Stop playback
   */
  async function stop() {
    try {
      const response = await axios.post('/api/podcast/stop');

      if (response.data.success) {
        currentEpisode.value = null;
        isPlaying.value = false;
        isBuffering.value = false;
        currentPosition.value = 0;
        currentDuration.value = 0;

        console.log('⏹️ Stopped');
        return true;
      }

      return false;

    } catch (error) {
      console.error('❌ Error stopping:', error);
      return false;
    }
  }

  /**
   * Subscribe to a podcast
   */
  async function subscribe(podcastUuid) {
    try {
      const response = await axios.post('/api/podcast/subscribe', {
        podcast_uuid: podcastUuid
      });

      if (response.data.success) {
        // Reload subscriptions
        await loadSubscriptions();
        console.log('✅ Subscribed');
        return true;
      }

      return false;

    } catch (error) {
      console.error('❌ Error subscribing:', error);
      return false;
    }
  }

  /**
   * Unsubscribe from a podcast
   */
  async function unsubscribe(podcastUuid) {
    try {
      const response = await axios.post('/api/podcast/unsubscribe', {
        podcast_uuid: podcastUuid
      });

      if (response.data.success) {
        // Remove from local list
        subscriptions.value = subscriptions.value.filter(s => s.uuid !== podcastUuid);
        console.log('✅ Unsubscribed');
        return true;
      }

      return false;

    } catch (error) {
      console.error('❌ Error unsubscribing:', error);
      return false;
    }
  }

  /**
   * Load all subscriptions
   */
  async function loadSubscriptions() {
    loadingSubscriptions.value = true;

    try {
      const response = await axios.get('/api/podcast/subscriptions');
      subscriptions.value = response.data.podcasts || [];

      console.log(`✅ Loaded ${subscriptions.value.length} subscriptions`);
      return true;

    } catch (error) {
      console.error('❌ Error loading subscriptions:', error);
      return false;

    } finally {
      loadingSubscriptions.value = false;
    }
  }

  /**
   * Add episode to favorites
   */
  async function addFavorite(episodeUuid) {
    try {
      const response = await axios.post('/api/podcast/favorites/add', {
        episode_uuid: episodeUuid
      });

      if (response.data.success) {
        // Reload favorites
        await loadFavorites();
        console.log('✅ Added to favorites');
        return true;
      }

      return false;

    } catch (error) {
      console.error('❌ Error adding favorite:', error);
      return false;
    }
  }

  /**
   * Remove episode from favorites
   */
  async function removeFavorite(episodeUuid) {
    try {
      const response = await axios.post('/api/podcast/favorites/remove', {
        episode_uuid: episodeUuid
      });

      if (response.data.success) {
        // Remove from local list
        favorites.value = favorites.value.filter(e => e.uuid !== episodeUuid);
        console.log('✅ Removed from favorites');
        return true;
      }

      return false;

    } catch (error) {
      console.error('❌ Error removing favorite:', error);
      return false;
    }
  }

  /**
   * Load all favorites
   */
  async function loadFavorites() {
    loadingFavorites.value = true;

    try {
      const response = await axios.get('/api/podcast/favorites');
      favorites.value = response.data.episodes || [];

      console.log(`✅ Loaded ${favorites.value.length} favorites`);
      return true;

    } catch (error) {
      console.error('❌ Error loading favorites:', error);
      return false;

    } finally {
      loadingFavorites.value = false;
    }
  }

  /**
   * Get current playback status from backend
   */
  async function refreshStatus() {
    try {
      const response = await axios.get('/api/podcast/status');
      const status = response.data;

      if (status.current_episode) {
        currentEpisode.value = status.current_episode;
        isPlaying.value = status.is_playing;
        isBuffering.value = status.is_buffering;
        currentPosition.value = status.position || 0;
        currentDuration.value = status.duration || 0;
      } else {
        currentEpisode.value = null;
        isPlaying.value = false;
        isBuffering.value = false;
        currentPosition.value = 0;
        currentDuration.value = 0;
      }

      return true;

    } catch (error) {
      console.error('❌ Error refreshing status:', error);
      return false;
    }
  }

  /**
   * Handle WebSocket state updates
   */
  function handleStateUpdate(state) {
    if (state.active_source !== 'podcast') {
      // Podcast is not active, reset state
      if (currentEpisode.value) {
        currentEpisode.value = null;
        isPlaying.value = false;
        isBuffering.value = false;
        currentPosition.value = 0;
        currentDuration.value = 0;
      }
      return;
    }

    // Podcast is active, update state
    const metadata = state.metadata || {};

    if (metadata.episode_uuid) {
      // Update current episode if changed
      if (!currentEpisode.value || currentEpisode.value.uuid !== metadata.episode_uuid) {
        currentEpisode.value = {
          uuid: metadata.episode_uuid,
          name: metadata.episode_name,
          description: metadata.description,
          image_url: metadata.image_url,
          podcast: {
            uuid: metadata.podcast_uuid,
            name: metadata.podcast_name
          }
        };
      }

      isPlaying.value = metadata.is_playing === true;
      isBuffering.value = metadata.is_buffering === true;
      currentPosition.value = metadata.position || 0;
      currentDuration.value = metadata.duration || 0;
    }
  }

  /**
   * Load more results (next page)
   */
  async function loadMoreResults() {
    if (isLoadingMore.value || !hasMoreResults.value) return;

    currentPage.value++;
    await search(searchQuery.value, searchType.value);
  }

  /**
   * Reset filters to defaults
   */
  function resetFilters() {
    countryFilter.value = '';
    genreFilter.value = '';
    languageFilter.value = '';
    sortBy.value = 'EXACTNESS';
    currentPage.value = 1;
    hasMoreResults.value = true;
  }

  /**
   * Reset store to initial state
   */
  function reset() {
    currentEpisode.value = null;
    isPlaying.value = false;
    isBuffering.value = false;
    currentPosition.value = 0;
    currentDuration.value = 0;
    searchQuery.value = '';
    searchResults.value = [];
    selectedPodcast.value = null;
    selectedPodcastEpisodes.value = [];
    episodesPage.value = 1;
    hasMoreEpisodes.value = false;
    resetFilters();
  }

  return {
    // State
    currentEpisode,
    isPlaying,
    isBuffering,
    currentPosition,
    currentDuration,
    searchQuery,
    searchResults,
    searchType,
    loading,
    hasError,
    subscriptions,
    loadingSubscriptions,
    favorites,
    loadingFavorites,
    selectedPodcast,
    selectedPodcastEpisodes,
    episodesPage,
    hasMoreEpisodes,
    loadingEpisodes,

    // Filters
    countryFilter,
    genreFilter,
    languageFilter,
    sortBy,

    // Pagination
    currentPage,
    hasMoreResults,
    isLoadingMore,

    // Getters
    isSubscribed,
    isFavorite,
    playbackProgress,
    formattedPosition,
    formattedDuration,

    // Actions
    search,
    getPodcastSeries,
    loadMoreEpisodes,
    loadMoreResults,
    playEpisode,
    pause,
    resume,
    seek,
    stop,
    subscribe,
    unsubscribe,
    loadSubscriptions,
    addFavorite,
    removeFavorite,
    loadFavorites,
    refreshStatus,
    handleStateUpdate,
    resetFilters,
    reset,

    // Helpers
    formatTime
  };
});
