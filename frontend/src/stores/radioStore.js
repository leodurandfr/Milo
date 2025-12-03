// frontend/src/stores/radioStore.js
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import axios from 'axios';

export const useRadioStore = defineStore('radio', () => {
  // === STATE (NORMALIZED) ===
  // Single source of truth: Map of all stations by ID
  const stations = ref(new Map());

  // Favorite station IDs
  const favoriteStationIds = ref(new Set());

  // Currently playing station ID
  const currentStationId = ref(null);

  // UI state
  const loading = ref(false);
  const hasError = ref(false);

  // Active filters
  const searchQuery = ref('');
  const countryFilter = ref('');
  const genreFilter = ref('');

  // Search result IDs (ordered by API response)
  const searchResultIds = ref([]);
  const currentTotal = ref(0);

  // Visible station IDs (for progressive rendering)
  const visibleStationIds = ref([]);

  // Top stations cache (for instant display when no filters are applied)
  const topStationsCache = ref(null);
  const topStationsCacheTimestamp = ref(null);
  const CACHE_DURATION_MS = 10 * 60 * 1000; // 10 minutes
  const BACKGROUND_REFRESH_THRESHOLD_MS = 5 * 60 * 1000; // 5 minutes
  const LOCALSTORAGE_KEY = 'milo_radio_top_stations';
  const LOCALSTORAGE_TIMESTAMP_KEY = 'milo_radio_top_stations_timestamp';

  // === LOCALSTORAGE HELPERS ===

  /**
   * Load top stations from localStorage
   * Returns { stations, timestamp } or null if not found/invalid
   */
  function loadFromLocalStorage() {
    try {
      const stationsJson = localStorage.getItem(LOCALSTORAGE_KEY);
      const timestampStr = localStorage.getItem(LOCALSTORAGE_TIMESTAMP_KEY);

      if (!stationsJson || !timestampStr) {
        return null;
      }

      const stations = JSON.parse(stationsJson);
      const timestamp = parseInt(timestampStr, 10);

      if (!Array.isArray(stations) || isNaN(timestamp)) {
        return null;
      }

      return { stations, timestamp };
    } catch (error) {
      console.warn('⚠️ Failed to load from localStorage:', error.message);
      return null;
    }
  }

  /**
   * Save top stations to localStorage
   */
  function saveToLocalStorage(stations, timestamp) {
    try {
      localStorage.setItem(LOCALSTORAGE_KEY, JSON.stringify(stations));
      localStorage.setItem(LOCALSTORAGE_TIMESTAMP_KEY, timestamp.toString());
    } catch (error) {
      // Quota exceeded or localStorage unavailable
      console.warn('⚠️ Failed to save to localStorage:', error.message);
    }
  }

  // AbortController to cancel ongoing requests
  let currentAbortController = null;

  // === HELPER FUNCTIONS ===

  /**
   * Add or update station in the map
   */
  function upsertStation(station) {
    const enrichedStation = {
      ...station,
      is_favorite: favoriteStationIds.value.has(station.id)
    };
    stations.value.set(station.id, enrichedStation);
  }

  /**
   * Add multiple stations to the map
   */
  function upsertStations(stationList) {
    stationList.forEach(station => upsertStation(station));
  }

  /**
   * Get station by ID with is_favorite enrichment
   */
  function getStation(stationId) {
    const station = stations.value.get(stationId);
    if (!station) return null;
    return {
      ...station,
      is_favorite: favoriteStationIds.value.has(stationId)
    };
  }

  // === COMPUTED PROPERTIES (API COMPATIBILITY) ===

  // Currently playing station (backward compatible)
  const currentStation = computed(() => {
    if (!currentStationId.value) return null;
    return getStation(currentStationId.value);
  });

  // All search result stations
  const currentStations = computed(() => {
    return searchResultIds.value
      .map(id => getStation(id))
      .filter(Boolean);
  });

  // Total available stations
  const totalStations = computed(() => currentTotal.value);

  // Displayed stations (progressively accumulated)
  const displayedStations = computed(() => {
    return visibleStationIds.value
      .map(id => getStation(id))
      .filter(Boolean);
  });

  // Are there more stations to show?
  const hasMoreStations = computed(() => {
    return visibleStationIds.value.length < searchResultIds.value.length;
  });

  // Remaining stations count
  const remainingStations = computed(() => {
    return Math.max(0, searchResultIds.value.length - visibleStationIds.value.length);
  });

  // Sorted favorite stations
  const sortedFavorites = computed(() => {
    return Array.from(favoriteStationIds.value)
      .map(id => getStation(id))
      .filter(Boolean)
      .sort((a, b) => a.name.localeCompare(b.name));
  });

  // === ACTIONS ===

  /**
   * Load stations according to active filters
   * Uses cache when available (top stations only)
   */
  async function loadStations(favoritesOnly = false) {
    // IMPORTANT: Set loading state immediately to prevent flash of "no stations" message
    loading.value = true;
    hasError.value = false;

    if (favoritesOnly) {
      // Load favorites
      try {
        const response = await axios.get('/api/radio/stations', {
          params: { limit: 10000, favorites_only: true }
        });

        // Update normalized store
        upsertStations(response.data.stations);
        favoriteStationIds.value = new Set(response.data.stations.map(s => s.id));

        console.log(`✅ Loaded ${response.data.stations.length} favorites`);
        return true;
      } catch (error) {
        console.error('❌ Error loading favorites:', error);
        hasError.value = true;
        return false;
      } finally {
        loading.value = false;
      }
    }

    // Check if this is a top stations request (no filters applied)
    const isTopStationsRequest = !searchQuery.value && !countryFilter.value && !genreFilter.value;

    // If requesting top stations, check cache first
    if (isTopStationsRequest) {
      // Try memory cache first
      if (topStationsCache.value && topStationsCacheTimestamp.value) {
        const cacheAge = Date.now() - topStationsCacheTimestamp.value;

        if (cacheAge < CACHE_DURATION_MS) {
          // Cache is valid, use it immediately
          console.log(`✅ Using cached top stations (age: ${Math.round(cacheAge / 1000)}s)`);

          // Update normalized store
          upsertStations(topStationsCache.value);
          searchResultIds.value = topStationsCache.value.map(s => s.id);
          currentTotal.value = topStationsCache.value.length;
          visibleStationIds.value = searchResultIds.value.slice(0, 40);

          // If cache is getting old (> 5 min), refresh in background
          if (cacheAge > BACKGROUND_REFRESH_THRESHOLD_MS) {
            console.log('🔄 Cache is aging, triggering background refresh');
            refreshTopStationsInBackground();
          }

          loading.value = false;
          return true;
        }

        // Cache expired, proceed with normal API call
        console.log(`⏰ Cache expired (age: ${Math.round(cacheAge / 1000)}s), fetching fresh data`);
      } else {
        // Memory cache empty, try localStorage as fallback
        const localStorageData = loadFromLocalStorage();

        if (localStorageData) {
          const { stations: cachedStations, timestamp } = localStorageData;
          const cacheAge = Date.now() - timestamp;

          console.log(`📦 Using localStorage fallback (age: ${Math.round(cacheAge / 1000)}s, ${cachedStations.length} stations)`);

          // Update normalized store
          upsertStations(cachedStations);
          searchResultIds.value = cachedStations.map(s => s.id);
          currentTotal.value = cachedStations.length;
          visibleStationIds.value = searchResultIds.value.slice(0, 40);

          // Also populate memory cache
          topStationsCache.value = cachedStations;
          topStationsCacheTimestamp.value = timestamp;

          // Always refresh in background when using localStorage
          // (since it could be old data from previous session)
          console.log('🔄 Triggering background refresh to get latest data');
          refreshTopStationsInBackground();

          loading.value = false;
          return true;
        }

        console.log('📭 No cache available, fetching from API');
      }
    }

    // Cancel previous request if it exists
    if (currentAbortController) {
      console.log('🚫 Cancelling previous search request');
      currentAbortController.abort();
    }

    // Create a new AbortController for this request
    currentAbortController = new AbortController();
    const signal = currentAbortController.signal;

    // Load from API (loading already set to true at function start)

    // IMPORTANT: Clear old data BEFORE API call to prevent showing stale data during loading
    visibleStationIds.value = [];
    searchResultIds.value = [];
    currentTotal.value = 0;

    try {
      const params = {
        limit: 10000,
        favorites_only: false
      };

      if (searchQuery.value) params.query = searchQuery.value;
      if (countryFilter.value) params.country = countryFilter.value;
      if (genreFilter.value) params.genre = genreFilter.value;

      console.log(`📻 Fetching stations from API`);
      const response = await axios.get('/api/radio/stations', { params, signal });

      // Update normalized store
      upsertStations(response.data.stations);
      searchResultIds.value = response.data.stations.map(s => s.id);
      currentTotal.value = response.data.total;

      // Initialize visible stations with the first 40
      visibleStationIds.value = searchResultIds.value.slice(0, 40);

      // If this was a top stations request, cache the results
      if (isTopStationsRequest) {
        const now = Date.now();
        topStationsCache.value = response.data.stations;
        topStationsCacheTimestamp.value = now;
        saveToLocalStorage(response.data.stations, now);
        console.log(`💾 Cached ${response.data.stations.length} top stations (memory + localStorage)`);
      }

      console.log(`✅ Loaded ${response.data.stations.length} stations (total: ${response.data.total})`);
      return true;
    } catch (error) {
      // If the request was canceled, don't log as an error
      if (axios.isCancel(error) || error.name === 'CanceledError') {
        console.log('🚫 Search request cancelled');
        return false;
      }

      console.error('❌ Error loading stations:', error);
      hasError.value = true;
      searchResultIds.value = [];
      currentTotal.value = 0;
      visibleStationIds.value = [];
      return false;
    } finally {
      loading.value = false;
      currentAbortController = null;
    }
  }

  /**
   * Refresh top stations in background without blocking UI
   * Used when cache is getting old but still valid
   */
  async function refreshTopStationsInBackground() {
    console.log('🔄 Starting background refresh of top stations');

    try {
      const response = await axios.get('/api/radio/stations', {
        params: { limit: 10000, favorites_only: false }
      });

      // Update cache silently
      const now = Date.now();
      topStationsCache.value = response.data.stations;
      topStationsCacheTimestamp.value = now;
      saveToLocalStorage(response.data.stations, now);

      // If user is still viewing top stations (no filters), update the display
      const isStillViewingTopStations = !searchQuery.value && !countryFilter.value && !genreFilter.value;
      if (isStillViewingTopStations && searchResultIds.value.length > 0) {
        // Only update if data actually changed (compare first station ID)
        const firstCurrentId = searchResultIds.value[0];
        const firstNewId = response.data.stations[0]?.id;

        if (firstCurrentId !== firstNewId) {
          upsertStations(response.data.stations);
          searchResultIds.value = response.data.stations.map(s => s.id);
          currentTotal.value = response.data.total;
          // Preserve current scroll position by keeping visibleStationIds as is
          console.log('✅ Background refresh complete, display updated');
        } else {
          console.log('✅ Background refresh complete, no changes');
        }
      } else {
        console.log('✅ Background refresh complete, cache updated');
      }
    } catch (error) {
      // Silent failure, keep using old cache
      console.warn('⚠️ Background refresh failed, keeping old cache:', error.message);
    }
  }

  /**
   * Load more stations (local pagination with progressive accumulation)
   */
  function loadMore() {
    const increment = 40;
    const currentVisible = visibleStationIds.value.length;
    const maxAvailable = searchResultIds.value.length;

    // Calculate how many we can add
    const newLimit = Math.min(currentVisible + increment, maxAvailable);

    // Add new station IDs to the visible list
    visibleStationIds.value = searchResultIds.value.slice(0, newLimit);

    console.log(`📻 Load more: displaying ${visibleStationIds.value.length} / ${maxAvailable} stations (added ${newLimit - currentVisible})`);
  }


  async function playStation(stationId) {
    try {
      const response = await axios.post('/api/radio/play', { station_id: stationId });
      return response.data.success;
    } catch (error) {
      console.error('Error playing station:', error);
      return false;
    }
  }

  async function stopPlayback() {
    try {
      const response = await axios.post('/api/radio/stop');
      return response.data.success;
    } catch (error) {
      console.error('Error stopping playback:', error);
      return false;
    }
  }

  async function addFavorite(stationId) {
    try {
      // Get station from normalized store
      const station = getStation(stationId);

      // Send the full station (or just the ID if not found)
      const payload = station
        ? { station_id: stationId, station: station }
        : { station_id: stationId };

      const response = await axios.post('/api/radio/favorites/add', payload);
      return response.data.success;
    } catch (error) {
      console.error('Error adding favorite:', error);
      return false;
    }
  }

  async function removeFavorite(stationId) {
    try {
      const response = await axios.post('/api/radio/favorites/remove', { station_id: stationId });
      return response.data.success;
    } catch (error) {
      console.error('Error removing favorite:', error);
      return false;
    }
  }

  async function toggleFavorite(stationId) {
    // Get station from normalized store
    const station = getStation(stationId);

    if (!station) {
      console.warn('toggleFavorite: station not found', stationId);
      return false;
    }

    if (station.is_favorite) {
      return await removeFavorite(stationId);
    } else {
      return await addFavorite(stationId);
    }
  }

  async function markBroken(stationId) {
    try {
      const response = await axios.post('/api/radio/broken/mark', { station_id: stationId });

      if (response.data.success) {
        // Remove from visible and search results
        visibleStationIds.value = visibleStationIds.value.filter(id => id !== stationId);
        searchResultIds.value = searchResultIds.value.filter(id => id !== stationId);

        // Remove from normalized store
        stations.value.delete(stationId);

        return true;
      }
      return false;
    } catch (error) {
      console.error('Error marking station as broken:', error);
      return false;
    }
  }

  async function resetBrokenStations() {
    try {
      const response = await axios.post('/api/radio/broken/reset');
      if (response.data.success) {
        await loadStations();
        return true;
      }
      return false;
    } catch (error) {
      console.error('Error resetting broken stations:', error);
      return false;
    }
  }

  async function addCustomStation(stationData) {
    /**
     * Add a custom station with image upload
     */
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
        headers: {
          'Content-Type': 'multipart/form-data'
        }
      });

      if (response.data.success) {
        const newStation = response.data.station;
        console.log('📻 Custom station added:', newStation);
        return { success: true, station: newStation };
      } else {
        return { success: false, error: response.data.error || 'Failed to add station' };
      }
    } catch (error) {
      console.error('Error adding custom station:', error);
      const errorMessage = error.response?.data?.detail || error.message || 'Unknown error';
      return { success: false, error: errorMessage };
    }
  }

  async function removeCustomStation(stationId) {
    try {
      const response = await axios.post('/api/radio/custom/remove', { station_id: stationId });

      if (response.data.success) {
        console.log('📻 Custom station removed:', stationId);

        // Remove from visible and search results
        visibleStationIds.value = visibleStationIds.value.filter(id => id !== stationId);
        searchResultIds.value = searchResultIds.value.filter(id => id !== stationId);

        // Remove from normalized store
        stations.value.delete(stationId);

        return true;
      }
      return false;
    } catch (error) {
      console.error('Error removing custom station:', error);
      return false;
    }
  }

  async function removeStationImage(stationId) {
    /**
     * Remove the imported image of a station
     */
    try {
      const formData = new FormData();
      formData.append('station_id', stationId);

      const response = await axios.post('/api/radio/custom/remove-image', formData, {
        headers: {
          'Content-Type': 'multipart/form-data'
        }
      });

      if (response.data.success) {
        const updatedStation = response.data.station;
        console.log('🖼️ Image removed:', stationId);

        // Update in normalized store
        upsertStation(updatedStation);

        return { success: true, station: updatedStation };
      } else {
        return { success: false, error: response.data.error || 'Failed to remove image' };
      }
    } catch (error) {
      console.error('Error removing station image:', error);
      const errorMessage = error.response?.data?.detail || error.message || 'Unknown error';
      return { success: false, error: errorMessage };
    }
  }

  function updateFromWebSocket(metadata) {
    // Update from WebSocket (via unifiedAudioStore)
    if (metadata.station_id) {
      // Get station from normalized store
      let station = getStation(metadata.station_id);

      if (station) {
        currentStationId.value = metadata.station_id;
      } else {
        // Station not yet loaded, create a minimal object
        const minimalStation = {
          id: metadata.station_id,
          name: metadata.station_name || 'Station inconnue',
          country: metadata.country || '',
          genre: metadata.genre || '',
          favicon: metadata.favicon || '',
          is_favorite: metadata.is_favorite || false
        };
        upsertStation(minimalStation);
        currentStationId.value = metadata.station_id;
      }
    } else {
      // No station playing - DON'T clear immediately to allow animation
      // The timer will handle clearing after animation completes
    }
  }

  async function handleFavoriteEvent(stationId, isFavorite) {
    // Sync favorite status from the backend
    console.log(`🔄 Syncing favorite status: ${stationId} = ${isFavorite}`);

    // Update favorite status in normalized store
    if (isFavorite) {
      favoriteStationIds.value.add(stationId);
    } else {
      favoriteStationIds.value.delete(stationId);
    }

    // If station exists in map, update it
    const station = stations.value.get(stationId);
    if (station) {
      station.is_favorite = isFavorite;
    }

    // If added to favorites and station not in map, reload favorites
    if (isFavorite && !station) {
      console.log('📻 New favorite added, reloading favorites');
      await loadStations(true);
    }
  }

  function clearCurrentStation() {
    // Clear current station
    console.log('📻 Clearing current station');
    currentStationId.value = null;
  }

  return {
    // State
    currentStation,
    loading,
    hasError,
    searchQuery,
    countryFilter,
    genreFilter,

    // Getters
    currentStations,
    totalStations,
    displayedStations,
    hasMoreStations,
    remainingStations,
    favoriteStations: sortedFavorites,

    // Actions
    loadStations,
    loadMore,
    playStation,
    stopPlayback,
    addFavorite,
    removeFavorite,
    toggleFavorite,
    markBroken,
    resetBrokenStations,
    addCustomStation,
    removeCustomStation,
    removeStationImage,
    updateFromWebSocket,
    handleFavoriteEvent,
    clearCurrentStation
  };
});