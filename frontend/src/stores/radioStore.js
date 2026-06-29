// frontend/src/stores/radioStore.js
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import { useUnifiedAudioStore } from './unifiedAudioStore';
import { logger } from '@/services/logger';
import { apiCall } from '@/services/apiCall';

export const useRadioStore = defineStore('radio', () => {
  // === STATE ===

  // Search results (simple array from API)
  const searchResults = ref([]);

  // How many stations to display (progressive rendering)
  const displayedCount = ref(40);

  // Total available from last search
  const totalResults = ref(0);

  // Favorite stations (dedicated storage, loaded from backend)
  const favoriteStations = ref([]);

  // UI state
  const loading = ref(false);
  const hasError = ref(false);
  const networkError = ref(false);
  const favoritesInitialized = ref(false);

  // Auto-retry timer for network errors — bounded: it gives up after
  // MAX_RETRY_ATTEMPTS or as soon as the user leaves the radio source
  // (the store never unmounts), the error state offers a manual retry.
  let retryTimer = null;
  let retryAttempts = 0;
  const RETRY_INTERVAL_MS = 5000;
  const MAX_RETRY_ATTEMPTS = 12;

  // Active filters
  const searchQuery = ref('');
  const countryFilter = ref('');
  const genreFilter = ref('');

  // Custom stations dict for settings view (modified + manually added)
  const customStations = ref({});

  // Top stations cache (3 minutes, memory only)
  const topStationsCache = ref(null);
  const topStationsCacheTimestamp = ref(null);
  const CACHE_DURATION_MS = 3 * 60 * 1000; // 3 minutes

  // AbortController to cancel ongoing requests
  let currentAbortController = null;

  // === COMPUTED PROPERTIES ===

  // Currently playing station (from unified store metadata, with property normalization)
  // Enriched with local favoriteStations data for immediate updates after metadata modifications
  const currentStation = computed(() => {
    const unifiedStore = useUnifiedAudioStore();

    // Only return station when radio is the active source
    if (unifiedStore.systemState.active_source !== 'radio') {
      return null;
    }

    const metadata = unifiedStore.systemState.metadata;
    if (!metadata?.station_id) {
      return null;
    }

    // Check if we have local metadata (from favoriteStations) - this has the most up-to-date info after modifications
    const localStation = favoriteStations.value.find(s => s.id === metadata.station_id);

    // Use local metadata if available (more up-to-date after modifications), fallback to WebSocket metadata
    return {
      id: metadata.station_id,
      name: localStation?.name ?? metadata.station_name,
      url: localStation?.url ?? metadata.station_url,
      country: localStation?.country ?? metadata.country,
      genre: localStation?.genre ?? metadata.genre,
      favicon: localStation?.favicon ?? metadata.favicon,
      bitrate: localStation?.bitrate ?? metadata.bitrate,
      codec: localStation?.codec ?? metadata.codec,
      is_favorite: isFavorite(metadata.station_id)
    };
  });

  // Currently recognized track info from Shazam (from unified store metadata)
  const trackInfo = computed(() => {
    const unifiedStore = useUnifiedAudioStore();

    if (unifiedStore.systemState.active_source !== 'radio') {
      return null;
    }

    const metadata = unifiedStore.systemState.metadata;
    if (!metadata?.track_title) {
      return null;
    }

    return {
      title: metadata.track_title,
      artist: metadata.track_artist || '',
      artwork: metadata.track_artwork || null
    };
  });

  // Displayed stations (slice of search results for progressive rendering)
  const displayedStations = computed(() => {
    return searchResults.value
      .slice(0, displayedCount.value)
      .map(station => ({
        ...station,
        is_favorite: isFavorite(station.id)
      }));
  });

  const hasMoreStations = computed(() => {
    return displayedCount.value < searchResults.value.length;
  });

  const sortedFavorites = computed(() => {
    return [...favoriteStations.value]
      .map(station => ({ ...station, is_favorite: true }))
      .sort((a, b) => a.name.localeCompare(b.name));
  });

  // === HELPER FUNCTIONS ===

  /**
   * Check if a station is in favorites
   */
  function isFavorite(stationId) {
    return favoriteStations.value.some(s => s.id === stationId);
  }

  /**
   * Check if top stations cache is valid
   */
  function isTopStationsCacheValid() {
    if (!topStationsCache.value || !topStationsCacheTimestamp.value) {
      return false;
    }
    const cacheAge = Date.now() - topStationsCacheTimestamp.value;
    return cacheAge < CACHE_DURATION_MS;
  }

  // === ACTIONS ===

  /**
   * Set loading state (used by components to indicate loading before async operations)
   */
  function setLoading(value) {
    loading.value = value;
  }

  /**
   * Reset search filters to defaults
   */
  function resetFilters() {
    searchQuery.value = '';
    countryFilter.value = '';
    genreFilter.value = '';
  }

  function startRetry() {
    if (retryTimer !== null) return;
    retryAttempts = 0;
    retryTimer = setInterval(() => {
      if (loading.value) return; // Prevent concurrent requests
      const unifiedStore = useUnifiedAudioStore();
      if (unifiedStore.systemState.active_source !== 'radio' || retryAttempts >= MAX_RETRY_ATTEMPTS) {
        stopRetry();
        return;
      }
      retryAttempts += 1;
      logger.debug('radio', `Auto-retrying after network error (${retryAttempts}/${MAX_RETRY_ATTEMPTS})...`);
      loadStations(false);
    }, RETRY_INTERVAL_MS);
  }

  function stopRetry() {
    if (retryTimer !== null) {
      clearInterval(retryTimer);
      retryTimer = null;
    }
  }

  // Guard against concurrent preload calls
  let preloadPromise = null;

  /**
   * Preload favorites at app boot (fire-and-forget, like podcastStore.preloadSubscriptionsList)
   * Ensures favorites are available instantly when the user opens Radio.
   *
   * force=true refetches even when already initialized (reconnect/tab-visible
   * resync — favorite_* WS deltas may have been missed). favoritesInitialized
   * stays true during the refetch so FavoritesView doesn't flash skeletons.
   */
  async function preloadFavorites({ force = false } = {}) {
    if (favoritesInitialized.value && !force) return;
    if (preloadPromise) return preloadPromise;
    preloadPromise = (async () => {
      const result = await apiCall.get('/api/radio/stations', {
        category: 'radio',
        message: 'Error preloading favorites',
        params: { favorites_only: true },
      });
      if (result.ok) {
        favoriteStations.value = result.data.stations;
        favoritesInitialized.value = true;
        logger.debug('radio', `Preloaded ${favoriteStations.value.length} favorites`);
      }
    })();
    await preloadPromise;
    preloadPromise = null;
  }

  /**
   * Load stations according to active filters
   */
  async function loadStations(favoritesOnly = false) {
    loading.value = true;
    hasError.value = false;

    if (favoritesOnly) {
      const result = await apiCall.get('/api/radio/stations', {
        category: 'radio',
        message: 'Error loading favorites',
        params: { favorites_only: true }
      });
      loading.value = false;
      if (result.ok) {
        favoriteStations.value = result.data.stations;
        logger.debug('radio', `Loaded ${favoriteStations.value.length} favorites`);
        favoritesInitialized.value = true;
        return true;
      }
      hasError.value = true;
      return false;
    }

    // Check if this is a top stations request (no filters)
    const isTopStationsRequest = !searchQuery.value && !countryFilter.value && !genreFilter.value;

    // Use cache for top stations if valid
    if (isTopStationsRequest && isTopStationsCacheValid()) {
      const cacheAge = Math.round((Date.now() - topStationsCacheTimestamp.value) / 1000);
      logger.debug('radio', `Using cached top stations (age: ${cacheAge}s)`);

      searchResults.value = topStationsCache.value;
      totalResults.value = topStationsCache.value.length;
      displayedCount.value = 40;
      loading.value = false;
      return true;
    }

    // Cancel previous request if exists
    if (currentAbortController) {
      logger.debug('radio', 'Cancelling previous search request');
      currentAbortController.abort();
    }

    currentAbortController = new AbortController();
    const signal = currentAbortController.signal;

    // Clear old data before API call
    searchResults.value = [];
    totalResults.value = 0;
    displayedCount.value = 40;

    const params = { favorites_only: false };
    if (searchQuery.value) params.query = searchQuery.value;
    if (countryFilter.value) params.country = countryFilter.value;
    if (genreFilter.value) params.genre = genreFilter.value;

    logger.debug('radio', 'Fetching stations from API');
    const result = await apiCall.get('/api/radio/stations', {
      category: 'radio',
      message: 'Error loading stations',
      params,
      signal
    });

    loading.value = false;
    currentAbortController = null;

    if (result.ok) {
      if (result.data.network_error) {
        networkError.value = true;
        hasError.value = true;
        startRetry();
        return false;
      }

      networkError.value = false;
      stopRetry();
      searchResults.value = result.data.stations;
      totalResults.value = result.data.total;
      displayedCount.value = 40;

      if (isTopStationsRequest) {
        topStationsCache.value = result.data.stations;
        topStationsCacheTimestamp.value = Date.now();
        logger.debug('radio', `Cached ${result.data.stations.length} top stations`);
      }

      logger.debug('radio', `Loaded ${result.data.stations.length} stations`);
      return true;
    }

    // result.error === null means the request was cancelled (AbortController)
    if (result.error === null) {
      logger.debug('radio', 'Search request cancelled');
      return false;
    }

    hasError.value = true;
    searchResults.value = [];
    totalResults.value = 0;

    // status === null indicates a TCP-level failure (backend unreachable) → keep retrying
    if (result.error.status === null) {
      networkError.value = true;
      startRetry();
    } else {
      stopRetry();
    }
    return false;
  }

  /**
   * Load more stations (increment displayed count)
   */
  function loadMore() {
    const increment = 40;
    const newCount = Math.min(displayedCount.value + increment, searchResults.value.length);
    const added = newCount - displayedCount.value;

    displayedCount.value = newCount;
    logger.debug('radio', `Load more: displaying ${displayedCount.value} / ${searchResults.value.length} stations (added ${added})`);
  }

  /**
   * Play a station
   */
  async function playStation(stationId) {
    let station = searchResults.value.find(s => s.id === stationId);
    if (!station) station = favoriteStations.value.find(s => s.id === stationId);
    const payload = station ? { station_id: stationId, station } : { station_id: stationId };
    const result = await apiCall.post('/api/radio/play', payload, {
      category: 'radio',
      message: 'Error playing station',
    });
    return result.ok && result.data.success;
  }

  /**
   * Stop playback
   */
  async function stopPlayback() {
    const result = await apiCall.post('/api/radio/stop', null, {
      category: 'radio',
      message: 'Error stopping playback',
    });
    return result.ok && result.data.success;
  }

  /**
   * Add a station to favorites
   */
  async function addFavorite(stationId) {
    let station = searchResults.value.find(s => s.id === stationId);
    if (!station) station = favoriteStations.value.find(s => s.id === stationId);
    const payload = station ? { station_id: stationId, station } : { station_id: stationId };
    const result = await apiCall.post('/api/radio/favorites/add', payload, {
      category: 'radio',
      message: 'Error adding favorite',
    });
    return result.ok && result.data.success;
  }

  /**
   * Remove a station from favorites
   */
  async function removeFavorite(stationId) {
    const result = await apiCall.delete(`/api/radio/favorites/${stationId}`, {
      category: 'radio',
      message: 'Error removing favorite',
    });
    return result.ok && result.data.success;
  }

  /**
   * Toggle favorite status
   */
  async function toggleFavorite(stationId) {
    if (isFavorite(stationId)) {
      return await removeFavorite(stationId);
    } else {
      return await addFavorite(stationId);
    }
  }

  /**
   * Add a custom station
   */
  async function addCustomStation(stationData) {
    const formData = new FormData();
    formData.append('name', stationData.name);
    formData.append('url', stationData.url);
    formData.append('country', stationData.country || '');
    formData.append('countrycode', stationData.countrycode || '');
    formData.append('genre', stationData.genre || '');
    formData.append('bitrate', stationData.bitrate || 0);
    formData.append('codec', stationData.codec || '');
    formData.append('shazam_enabled', (stationData.shazam_enabled !== false).toString());

    if (stationData.image) {
      formData.append('image', stationData.image);
    }

    const result = await apiCall.post('/api/radio/custom/add', formData, {
      category: 'radio',
      message: 'Error adding custom station',
      headers: { 'Content-Type': 'multipart/form-data' }
    });

    if (!result.ok) {
      return { success: false, error: result.error?.detail || 'Failed to add station' };
    }
    if (result.data.success) {
      logger.info('radio', 'Custom station added', result.data.station);
      return { success: true, station: result.data.station };
    }
    return { success: false, error: result.data.error || 'Failed to add station' };
  }

  /**
   * Remove a custom station
   */
  async function removeCustomStation(stationId) {
    const result = await apiCall.delete(`/api/radio/custom/${stationId}`, {
      category: 'radio',
      message: 'Error removing custom station',
    });
    if (result.ok && result.data.success) {
      logger.info('radio', `Custom station removed: ${stationId}`);
      searchResults.value = searchResults.value.filter(s => s.id !== stationId);
      totalResults.value = Math.max(0, totalResults.value - 1);
      return true;
    }
    return false;
  }

  /**
   * Fetch custom stations dict from API (for settings view)
   */
  async function fetchCustomStations() {
    const result = await apiCall.get('/api/radio/custom', {
      category: 'radio',
      message: 'Error loading custom stations',
    });
    customStations.value = result.ok ? (result.data || {}) : {};
  }

  /**
   * Load radio settings data (custom stations only — favorites are already
   * kept fresh via preload + WebSocket events)
   */
  async function loadRadioSettingsData() {
    await fetchCustomStations();
  }

  /**
   * Handle favorite added/removed event from WebSocket
   */
  async function handleFavoriteEvent(stationId, isFavoriteNow) {
    logger.info('radio', `Syncing favorite status: ${stationId} = ${isFavoriteNow}`);

    if (isFavoriteNow) {
      const station = searchResults.value.find(s => s.id === stationId);
      if (station && !favoriteStations.value.some(s => s.id === stationId)) {
        favoriteStations.value = [...favoriteStations.value, { ...station, is_favorite: true }];
      } else if (!station) {
        // Station not in search results, reload favorites
        logger.debug('radio', 'New favorite not in cache, reloading favorites');
        await loadStations(true);
      }
    } else {
      // Remove from favorites - reload to get animation and ensure consistency
      logger.debug('radio', 'Favorite removed, reloading favorites');
      await loadStations(true);
    }
  }

  /**
   * Handle metadata modified event from WebSocket
   */
  function handleMetadataModified(updatedStation) {
    logger.debug('radio', `Station metadata modified: ${updatedStation.id}`);

    // Update in favoriteStations
    const favIndex = favoriteStations.value.findIndex(s => s.id === updatedStation.id);
    if (favIndex !== -1) {
      favoriteStations.value = [
        ...favoriteStations.value.slice(0, favIndex),
        { ...updatedStation, is_favorite: true },
        ...favoriteStations.value.slice(favIndex + 1)
      ];
    }

    // Update in searchResults
    const searchIndex = searchResults.value.findIndex(s => s.id === updatedStation.id);
    if (searchIndex !== -1) {
      searchResults.value = [
        ...searchResults.value.slice(0, searchIndex),
        { ...searchResults.value[searchIndex], ...updatedStation },
        ...searchResults.value.slice(searchIndex + 1)
      ];
    }
  }

  async function resync() {
    return preloadFavorites({ force: true });
  }

  return {
    resync,
    // State
    currentStation,
    trackInfo,
    loading,
    hasError,
    networkError,
    favoritesInitialized,
    searchQuery,
    countryFilter,
    genreFilter,

    // Getters
    displayedStations,
    hasMoreStations,
    favoriteStations: sortedFavorites,
    customStations,

    // Actions
    preloadFavorites,
    loadStations,
    loadRadioSettingsData,
    loadMore,
    playStation,
    stopPlayback,
    addFavorite,
    removeFavorite,
    toggleFavorite,
    addCustomStation,
    removeCustomStation,
    setLoading,
    resetFilters,
    handleFavoriteEvent,
    handleMetadataModified
  };
});
