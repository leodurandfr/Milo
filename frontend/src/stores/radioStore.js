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

  // Favorite stations (dedicated storage, loaded from backend)
  const favoriteStations = ref([]);

  // UI state
  const loading = ref(false);
  const hasError = ref(false);
  // Named after the outcome rather than a cause, because it has two: the
  // directory (radio-browser.info) answering `api_error`, and the backend
  // itself being unreachable. Either way the *search* is what stops working —
  // favourites are local and a tuned stream comes from the station's own host,
  // so neither is affected. A dead link is a different fact again, reported at
  // the source level by the state's `availability.radio`.
  const searchUnavailable = ref(false);
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

  // Custom stations dict for settings view (modified + manually added).
  // Only fetched once the settings view has asked for it; the loaded flag gates
  // the WS refresh and the resync so an unopened view costs nothing.
  const customStations = ref({});
  const customStationsLoaded = ref(false);

  // Top stations cache (3 minutes, memory only)
  const topStationsCache = ref(null);
  const topStationsCacheTimestamp = ref(null);
  const CACHE_DURATION_MS = 3 * 60 * 1000; // 3 minutes

  // AbortController to cancel ongoing requests
  let currentAbortController = null;

  // === COMPUTED PROPERTIES ===

  // The radio's content, while radio is the selected source: the station
  // (tuned, or the one `resume_playback` would re-tune) and the song
  // recognized in its stream.
  const radioDetails = computed(() => {
    const state = useUnifiedAudioStore().systemState;
    if (state.source !== 'radio' || state.details?.kind !== 'radio') return null;
    return state.details;
  });

  // The station on the wire, enriched with the local favorite record — a rename
  // done in the UI lands there at once, before the backend's next state.
  const currentStation = computed(() => {
    const station = radioDetails.value?.station;
    if (!station?.id) return null;
    const local = favoriteStations.value.find(s => s.id === station.id);
    return {
      id: station.id,
      name: local?.name ?? station.name,
      url: local?.url ?? station.url,
      country: local?.country ?? station.country,
      genre: local?.genre ?? station.genre,
      favicon: local?.favicon ?? station.favicon,
      bitrate: local?.bitrate ?? station.bitrate,
      codec: local?.codec ?? station.codec,
    };
  });

  // The recognized song (in-band or Shazam). A stopped radio carries none.
  const trackInfo = computed(() => {
    const track = radioDetails.value?.track;
    if (!track?.title) return null;
    return {
      title: track.title,
      artist: track.artist || '',
      artwork: track.artwork || null,
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

  // Display order is the backend's, not ours: `/api/radio/stations` already
  // returns the favorites sorted by name, and RadioSource's next/prev steps the
  // same list. A second sort here would let the grid and the physical buttons
  // disagree about which station is "the next one".
  const enrichedFavorites = computed(() => {
    return favoriteStations.value.map(station => ({ ...station, is_favorite: true }));
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
      if (unifiedStore.systemState.source !== 'radio' || retryAttempts >= MAX_RETRY_ATTEMPTS) {
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
      displayedCount.value = 40;
      loading.value = false;
      return true;
    }

    // Cancel previous request if exists
    if (currentAbortController) {
      logger.debug('radio', 'Cancelling previous search request');
      currentAbortController.abort();
    }

    // Held locally as well: after the await, `currentAbortController` may already
    // belong to a newer search, and only identity tells the two apart.
    const ctrl = new AbortController();
    currentAbortController = ctrl;
    const signal = ctrl.signal;

    // Clear old data before API call
    searchResults.value = [];
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

    // Superseded: a newer search aborted this one and now owns both the spinner
    // and the controller. Clearing them here would end its loading state early
    // and leave it unabortable.
    if (currentAbortController !== ctrl) {
      logger.debug('radio', 'Search request cancelled');
      return false;
    }

    loading.value = false;
    currentAbortController = null;

    if (result.ok) {
      if (result.data.api_error) {
        searchUnavailable.value = true;
        hasError.value = true;
        startRetry();
        return false;
      }

      searchUnavailable.value = false;
      stopRetry();
      searchResults.value = result.data.stations;
      displayedCount.value = 40;

      if (isTopStationsRequest) {
        topStationsCache.value = result.data.stations;
        topStationsCacheTimestamp.value = Date.now();
        logger.debug('radio', `Cached ${result.data.stations.length} top stations`);
      }

      logger.debug('radio', `Loaded ${result.data.stations.length} stations`);
      return true;
    }

    hasError.value = true;
    searchResults.value = [];

    // status === null indicates a TCP-level failure (backend unreachable) → keep retrying
    if (result.error.status === null) {
      searchUnavailable.value = true;
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
    const result = await apiCall.post('/api/radio/favorites', payload, {
      category: 'radio',
      message: 'Error adding favorite',
    });
    return result.ok;
  }

  /**
   * Remove a station from favorites
   */
  async function removeFavorite(stationId) {
    const result = await apiCall.delete(`/api/radio/favorites/${stationId}`, {
      category: 'radio',
      message: 'Error removing favorite',
    });
    return result.ok;
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

    const result = await apiCall.post('/api/radio/custom', formData, {
      category: 'radio',
      message: 'Error adding custom station',
      headers: { 'Content-Type': 'multipart/form-data' }
    });

    // The route raises on failure, so result.ok is the whole verdict.
    if (!result.ok) {
      // apiCall has already logged the backend's detail: what comes back here
      // is the verdict, and the wording is the component's to translate.
      return { success: false };
    }
    logger.info('radio', 'Custom station added', result.data.station);
    return { success: true, station: result.data.station };
  }

  /**
   * Remove a custom station
   */
  async function removeCustomStation(stationId) {
    const result = await apiCall.delete(`/api/radio/custom/${stationId}`, {
      category: 'radio',
      message: 'Error removing custom station',
    });
    // The route raises on failure, so result.ok is the whole verdict.
    if (result.ok) {
      logger.info('radio', `Custom station removed: ${stationId}`);
      searchResults.value = searchResults.value.filter(s => s.id !== stationId);
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
    customStationsLoaded.value = true;
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
      // Refetched rather than appended in place: the list comes ordered by the
      // backend, and an append would park the new station at the end of the
      // grid while next/prev already steps it at its real position. Through
      // preloadFavorites, not loadStations: the heart is usually tapped from
      // the search view, whose results share the `loading` flag — reloading
      // through it would blank the grid the user is looking at.
      await preloadFavorites({ force: true });
    } else {
      // Remove from favorites - reload to get animation and ensure consistency
      logger.debug('radio', 'Favorite removed, reloading favorites');
      await loadStations(true);
    }
  }

  /**
   * Handle metadata modified event from WebSocket
   */
  async function handleMetadataModified(updatedStation) {
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

    // Editing a station is what promotes it to (or updates it in) the custom
    // dict, so the whole dict is refetched rather than patched in place: the
    // event carries the station, not whether it is now a custom entry.
    if (customStationsLoaded.value) fetchCustomStations();

    // Last, because the three updates above are local and must not wait on a
    // request: the splice refreshed the card where it already sat, but a rename
    // moves the station in the backend's order — and that order is the one
    // next/prev steps. Resynced silently (see handleFavoriteEvent on the flag).
    if (favIndex !== -1) await preloadFavorites({ force: true });
  }

  async function resync() {
    await Promise.all([
      preloadFavorites({ force: true }),
      customStationsLoaded.value ? fetchCustomStations() : Promise.resolve(),
    ]);
  }

  return {
    resync,
    // State
    currentStation,
    trackInfo,
    loading,
    hasError,
    searchUnavailable,
    favoritesInitialized,
    searchQuery,
    countryFilter,
    genreFilter,

    // Getters
    displayedStations,
    hasMoreStations,
    favoriteStations: enrichedFavorites,
    customStations,

    // Actions
    preloadFavorites,
    loadStations,
    loadRadioSettingsData,
    loadMore,
    playStation,
    stopPlayback,
    toggleFavorite,
    addCustomStation,
    removeCustomStation,
    setLoading,
    resetFilters,
    handleFavoriteEvent,
    handleMetadataModified
  };
});
