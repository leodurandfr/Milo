// frontend/src/stores/radioStore.js
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import axios from 'axios';
import { useUnifiedAudioStore } from './unifiedAudioStore';

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
  const favoritesInitialized = ref(false);

  // Active filters
  const searchQuery = ref('');
  const countryFilter = ref('');
  const genreFilter = ref('');

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

  // Are there more stations to show?
  const hasMoreStations = computed(() => {
    return displayedCount.value < searchResults.value.length;
  });

  // Remaining stations count
  const remainingStations = computed(() => {
    return Math.max(0, searchResults.value.length - displayedCount.value);
  });

  // Total stations from last search
  const totalStations = computed(() => totalResults.value);

  // Sorted favorite stations
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
   * Load stations according to active filters
   */
  async function loadStations(favoritesOnly = false) {
    loading.value = true;
    hasError.value = false;

    if (favoritesOnly) {
      // Load favorites from backend
      try {
        const response = await axios.get('/api/radio/stations', {
          params: { favorites_only: true }
        });

        favoriteStations.value = response.data.stations;
        console.log(`✅ Loaded ${favoriteStations.value.length} favorites`);
        favoritesInitialized.value = true;
        return true;
      } catch (error) {
        console.error('❌ Error loading favorites:', error);
        hasError.value = true;
        return false;
      } finally {
        loading.value = false;
      }
    }

    // Check if this is a top stations request (no filters)
    const isTopStationsRequest = !searchQuery.value && !countryFilter.value && !genreFilter.value;

    // Use cache for top stations if valid
    if (isTopStationsRequest && isTopStationsCacheValid()) {
      const cacheAge = Math.round((Date.now() - topStationsCacheTimestamp.value) / 1000);
      console.log(`✅ Using cached top stations (age: ${cacheAge}s)`);

      searchResults.value = topStationsCache.value;
      totalResults.value = topStationsCache.value.length;
      displayedCount.value = 40;
      loading.value = false;
      return true;
    }

    // Cancel previous request if exists
    if (currentAbortController) {
      console.log('🚫 Cancelling previous search request');
      currentAbortController.abort();
    }

    currentAbortController = new AbortController();
    const signal = currentAbortController.signal;

    // Clear old data before API call
    searchResults.value = [];
    totalResults.value = 0;
    displayedCount.value = 40;

    try {
      const params = { favorites_only: false };

      if (searchQuery.value) params.query = searchQuery.value;
      if (countryFilter.value) params.country = countryFilter.value;
      if (genreFilter.value) params.genre = genreFilter.value;

      console.log('📻 Fetching stations from API');
      const response = await axios.get('/api/radio/stations', { params, signal });

      searchResults.value = response.data.stations;
      totalResults.value = response.data.total;
      displayedCount.value = 40;

      // Cache top stations
      if (isTopStationsRequest) {
        topStationsCache.value = response.data.stations;
        topStationsCacheTimestamp.value = Date.now();
        console.log(`💾 Cached ${response.data.stations.length} top stations`);
      }

      console.log(`✅ Loaded ${response.data.stations.length} stations`);
      return true;
    } catch (error) {
      if (axios.isCancel(error) || error.name === 'CanceledError') {
        console.log('🚫 Search request cancelled');
        return false;
      }

      console.error('❌ Error loading stations:', error);
      hasError.value = true;
      searchResults.value = [];
      totalResults.value = 0;
      return false;
    } finally {
      loading.value = false;
      currentAbortController = null;
    }
  }

  /**
   * Load more stations (increment displayed count)
   */
  function loadMore() {
    const increment = 40;
    const newCount = Math.min(displayedCount.value + increment, searchResults.value.length);
    const added = newCount - displayedCount.value;

    displayedCount.value = newCount;
    console.log(`📻 Load more: displaying ${displayedCount.value} / ${searchResults.value.length} stations (added ${added})`);
  }

  /**
   * Play a station
   */
  async function playStation(stationId) {
    try {
      // Find station in search results or favorites
      let station = searchResults.value.find(s => s.id === stationId);
      if (!station) {
        station = favoriteStations.value.find(s => s.id === stationId);
      }

      const payload = station
        ? { station_id: stationId, station }
        : { station_id: stationId };

      const response = await axios.post('/api/radio/play', payload);
      return response.data.success;
    } catch (error) {
      console.error('Error playing station:', error);
      return false;
    }
  }

  /**
   * Stop playback
   */
  async function stopPlayback() {
    try {
      const response = await axios.post('/api/radio/stop');
      return response.data.success;
    } catch (error) {
      console.error('Error stopping playback:', error);
      return false;
    }
  }

  /**
   * Add a station to favorites
   */
  async function addFavorite(stationId) {
    try {
      let station = searchResults.value.find(s => s.id === stationId);
      if (!station) {
        station = favoriteStations.value.find(s => s.id === stationId);
      }

      const payload = station
        ? { station_id: stationId, station }
        : { station_id: stationId };

      const response = await axios.post('/api/radio/favorites/add', payload);
      return response.data.success;
    } catch (error) {
      console.error('Error adding favorite:', error);
      return false;
    }
  }

  /**
   * Remove a station from favorites
   */
  async function removeFavorite(stationId) {
    try {
      const response = await axios.post('/api/radio/favorites/remove', { station_id: stationId });
      return response.data.success;
    } catch (error) {
      console.error('Error removing favorite:', error);
      return false;
    }
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
    try {
      const formData = new FormData();
      formData.append('name', stationData.name);
      formData.append('url', stationData.url);
      formData.append('country', stationData.country || '');
      formData.append('genre', stationData.genre || '');
      formData.append('bitrate', stationData.bitrate || 0);
      formData.append('codec', stationData.codec || '');

      if (stationData.image) {
        formData.append('image', stationData.image);
      }

      const response = await axios.post('/api/radio/custom/add', formData, {
        headers: { 'Content-Type': 'multipart/form-data' }
      });

      if (response.data.success) {
        console.log('📻 Custom station added:', response.data.station);
        return { success: true, station: response.data.station };
      } else {
        return { success: false, error: response.data.error || 'Failed to add station' };
      }
    } catch (error) {
      console.error('Error adding custom station:', error);
      const errorMessage = error.response?.data?.detail || error.message || 'Unknown error';
      return { success: false, error: errorMessage };
    }
  }

  /**
   * Remove a custom station
   */
  async function removeCustomStation(stationId) {
    try {
      const response = await axios.post('/api/radio/custom/remove', { station_id: stationId });

      if (response.data.success) {
        console.log('📻 Custom station removed:', stationId);

        // Remove from search results
        searchResults.value = searchResults.value.filter(s => s.id !== stationId);
        totalResults.value = Math.max(0, totalResults.value - 1);

        return true;
      }
      return false;
    } catch (error) {
      console.error('Error removing custom station:', error);
      return false;
    }
  }

  /**
   * Remove a station's image
   */
  async function removeStationImage(stationId) {
    try {
      const formData = new FormData();
      formData.append('station_id', stationId);

      const response = await axios.post('/api/radio/custom/remove-image', formData, {
        headers: { 'Content-Type': 'multipart/form-data' }
      });

      if (response.data.success) {
        console.log('🖼️ Image removed:', stationId);

        // Update in search results
        const index = searchResults.value.findIndex(s => s.id === stationId);
        if (index !== -1) {
          searchResults.value[index] = response.data.station;
        }

        return { success: true, station: response.data.station };
      } else {
        return { success: false, error: response.data.error || 'Failed to remove image' };
      }
    } catch (error) {
      console.error('Error removing station image:', error);
      const errorMessage = error.response?.data?.detail || error.message || 'Unknown error';
      return { success: false, error: errorMessage };
    }
  }

  /**
   * Update from WebSocket metadata (legacy - kept for compatibility)
   * No longer needs to store state as currentStation reads directly from unifiedStore
   */
  function updateFromWebSocket(metadata) {
    // No-op: currentStation now reads directly from unifiedStore.systemState.metadata
  }

  /**
   * Handle favorite added/removed event from WebSocket
   */
  async function handleFavoriteEvent(stationId, isFavoriteNow) {
    console.log(`🔄 Syncing favorite status: ${stationId} = ${isFavoriteNow}`);

    if (isFavoriteNow) {
      // Find station and add to favorites
      const station = searchResults.value.find(s => s.id === stationId);
      if (station && !favoriteStations.value.some(s => s.id === stationId)) {
        favoriteStations.value = [...favoriteStations.value, { ...station, is_favorite: true }];
      } else if (!station) {
        // Station not in search results, reload favorites
        console.log('📻 New favorite not in cache, reloading favorites');
        await loadStations(true);
      }
    } else {
      // Remove from favorites - reload to get animation and ensure consistency
      console.log('📻 Favorite removed, reloading favorites');
      await loadStations(true);
    }
  }

  /**
   * Handle metadata modified event from WebSocket
   */
  function handleMetadataModified(updatedStation) {
    console.log('📻 Station metadata modified:', updatedStation.id);

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

  /**
   * Clear current station (legacy - kept for compatibility)
   * No longer needs to clear local state as currentStation reads from unifiedStore
   */
  function clearCurrentStation() {
    // No-op: currentStation now reads directly from unifiedStore.systemState.metadata
  }

  return {
    // State
    currentStation,
    trackInfo,
    loading,
    hasError,
    favoritesInitialized,
    searchQuery,
    countryFilter,
    genreFilter,

    // Getters
    displayedStations,
    hasMoreStations,
    remainingStations,
    totalStations,
    favoriteStations: sortedFavorites,

    // Actions
    loadStations,
    loadMore,
    playStation,
    stopPlayback,
    addFavorite,
    removeFavorite,
    toggleFavorite,
    addCustomStation,
    removeCustomStation,
    removeStationImage,
    updateFromWebSocket,
    handleFavoriteEvent,
    handleMetadataModified,
    clearCurrentStation
  };
});
