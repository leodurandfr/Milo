// frontend/src/stores/radioStore.js
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import axios from 'axios';

export const useRadioStore = defineStore('radio', () => {
  // === STATE ===
  const currentStation = ref(null);
  const loading = ref(false);

  // Active filters
  const searchQuery = ref('');
  const countryFilter = ref('');
  const genreFilter = ref('');

  // Current stations (result of the latest search)
  const currentStations = ref([]);
  const currentTotal = ref(0);

  // Favorites (loaded once, reloaded on change)
  const favoriteStations = ref([]);

  // Stations currently visible (for progressive rendering)
  const visibleStations = ref([]);

  // AbortController to cancel ongoing requests
  let currentAbortController = null;

  // === GETTERS ===

  // Total available stations
  const totalStations = computed(() => currentTotal.value);

  // Displayed stations (progressively accumulated)
  const displayedStations = computed(() => visibleStations.value);

  // Are there more stations to show?
  const hasMoreStations = computed(() => {
    return visibleStations.value.length < currentStations.value.length;
  });

  // Remaining stations count
  const remainingStations = computed(() => {
    return Math.max(0, currentStations.value.length - visibleStations.value.length);
  });

  // Sorted favorite stations
  const sortedFavorites = computed(() => {
    return favoriteStations.value
      .filter(s => s.is_favorite)
      .sort((a, b) => a.name.localeCompare(b.name));
  });

  // === ACTIONS ===

  /**
   * Load stations according to active filters
   * Always makes an API call (no cache)
   */
  async function loadStations(favoritesOnly = false) {
    if (favoritesOnly) {
      // Load favorites
      loading.value = true;
      try {
        const response = await axios.get('/api/radio/stations', {
          params: { limit: 10000, favorites_only: true }
        });

        favoriteStations.value = response.data.stations;
        console.log(`✅ Loaded ${response.data.stations.length} favorites`);
        return true;
      } catch (error) {
        console.error('❌ Error loading favorites:', error);
        return false;
      } finally {
        loading.value = false;
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

    // Load from API
    loading.value = true;
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

      // Store stations
      currentStations.value = response.data.stations;
      currentTotal.value = response.data.total;

      // Initialize visible stations with the first 40
      visibleStations.value = response.data.stations.slice(0, 40);

      console.log(`✅ Loaded ${response.data.stations.length} stations (total: ${response.data.total})`);
      return true;
    } catch (error) {
      // If the request was canceled, don't log as an error
      if (axios.isCancel(error) || error.name === 'CanceledError') {
        console.log('🚫 Search request cancelled');
        return false;
      }

      console.error('❌ Error loading stations:', error);
      currentStations.value = [];
      currentTotal.value = 0;
      visibleStations.value = [];
      return false;
    } finally {
      loading.value = false;
      currentAbortController = null;
    }
  }

  /**
   * Load more stations (local pagination with progressive accumulation)
   */
  function loadMore() {
    const increment = 40;
    const currentVisible = visibleStations.value.length;
    const maxAvailable = currentStations.value.length;

    // Calculate how many we can add
    const newLimit = Math.min(currentVisible + increment, maxAvailable);

    // Add new stations to the visible list
    const newStations = currentStations.value.slice(currentVisible, newLimit);
    visibleStations.value = [...visibleStations.value, ...newStations];

    console.log(`📻 Load more: displaying ${visibleStations.value.length} / ${maxAvailable} stations (added ${newStations.length})`);
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
      // Find the full station object
      let station = null;

      // Search in visibleStations
      station = visibleStations.value.find(s => s.id === stationId);

      // If not found, search in currentStation
      if (!station && currentStation.value?.id === stationId) {
        station = currentStation.value;
      }

      // If not found, search in favoriteStations
      if (!station) {
        station = favoriteStations.value.find(s => s.id === stationId);
      }

      // If not found, search in currentStations
      if (!station) {
        station = currentStations.value.find(s => s.id === stationId);
      }

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
    // Find the station
    let station = currentStations.value.find(s => s.id === stationId);

    if (!station) {
      station = visibleStations.value.find(s => s.id === stationId);
    }

    if (!station && currentStation.value?.id === stationId) {
      station = currentStation.value;
    }

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
        // Remove from visibleStations
        visibleStations.value = visibleStations.value.filter(s => s.id !== stationId);

        // Remove from currentStations
        currentStations.value = currentStations.value.filter(s => s.id !== stationId);

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
      formData.append('country', stationData.country || 'France');
      formData.append('genre', stationData.genre || 'Variety');
      formData.append('bitrate', stationData.bitrate || 128);
      formData.append('codec', stationData.codec || 'MP3');

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

        // Remove from visibleStations and currentStations
        visibleStations.value = visibleStations.value.filter(s => s.id !== stationId);
        currentStations.value = currentStations.value.filter(s => s.id !== stationId);

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

        // Update in visibleStations
        const visibleIndex = visibleStations.value.findIndex(s => s.id === stationId);
        if (visibleIndex !== -1) {
          visibleStations.value[visibleIndex] = updatedStation;
        }

        // Update in currentStations
        const currentIndex = currentStations.value.findIndex(s => s.id === stationId);
        if (currentIndex !== -1) {
          currentStations.value[currentIndex] = updatedStation;
        }

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
      // Search the station in current stations
      let station = currentStations.value.find(s => s.id === metadata.station_id);

      // Otherwise, search in favorites
      if (!station) {
        station = favoriteStations.value.find(s => s.id === metadata.station_id);
      }

      if (station) {
        currentStation.value = station;
      } else {
        // Station not yet loaded, create a minimal object
        currentStation.value = {
          id: metadata.station_id,
          name: metadata.station_name || 'Station inconnue',
          country: metadata.country || '',
          genre: metadata.genre || '',
          favicon: metadata.favicon || '',
          is_favorite: metadata.is_favorite || false
        };
      }
    } else {
      // No station playing
      currentStation.value = null;
    }
  }

  async function handleFavoriteEvent(stationId, isFavorite) {
    // Sync favorite status from the backend
    console.log(`🔄 Syncing favorite status: ${stationId} = ${isFavorite}`);

    // Update in currentStations
    const currentStationObj = currentStations.value.find(s => s.id === stationId);
    if (currentStationObj) {
      currentStationObj.is_favorite = isFavorite;
    }

    // Update in visibleStations
    const visibleStation = visibleStations.value.find(s => s.id === stationId);
    if (visibleStation) {
      visibleStation.is_favorite = isFavorite;
    }

    // Update in favoriteStations
    const favoriteStation = favoriteStations.value.find(s => s.id === stationId);
    if (favoriteStation) {
      favoriteStation.is_favorite = isFavorite;
    }

    // If added to favorites, reload favorites
    if (isFavorite && !favoriteStation) {
      console.log('📻 New favorite added, reloading favorites');
      await loadStations(true);
    }

    // Update currentStation if it is the current one
    if (currentStation.value?.id === stationId) {
      currentStation.value.is_favorite = isFavorite;
    }
  }

  return {
    // State
    currentStation,
    loading,
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
    handleFavoriteEvent
  };
});