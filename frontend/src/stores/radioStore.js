// frontend/src/stores/radioStore.js
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import axios from 'axios';

export const useRadioStore = defineStore('radio', () => {
  // === ÉTAT ===
  const currentStation = ref(null);
  const loading = ref(false);

  // Filtres actifs
  const searchQuery = ref('');
  const countryFilter = ref('');
  const genreFilter = ref('');

  // Stations actuelles (résultat de la dernière recherche)
  const currentStations = ref([]);
  const currentTotal = ref(0);

  // Favoris (chargés une fois, rechargés si modification)
  const favoriteStations = ref([]);

  // Stations visibles actuellement (pour rendu progressif)
  const visibleStations = ref([]);

  // AbortController pour annuler les requêtes en cours
  let currentAbortController = null;

  // === GETTERS ===

  // Total de stations disponibles
  const totalStations = computed(() => currentTotal.value);

  // Stations affichées (accumulées progressivement)
  const displayedStations = computed(() => visibleStations.value);

  // Y a-t-il plus de stations à afficher ?
  const hasMoreStations = computed(() => {
    return visibleStations.value.length < currentStations.value.length;
  });

  // Nombre de stations restantes
  const remainingStations = computed(() => {
    return Math.max(0, currentStations.value.length - visibleStations.value.length);
  });

  // Stations favorites triées
  const sortedFavorites = computed(() => {
    return favoriteStations.value
      .filter(s => s.is_favorite)
      .sort((a, b) => a.name.localeCompare(b.name));
  });

  // === ACTIONS ===

  /**
   * Charge les stations selon les filtres actifs
   * Fait toujours un appel API (pas de cache)
   */
  async function loadStations(favoritesOnly = false) {
    if (favoritesOnly) {
      // Charger les favoris
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

    // Annuler la requête précédente si elle existe
    if (currentAbortController) {
      console.log('🚫 Cancelling previous search request');
      currentAbortController.abort();
    }

    // Créer un nouveau AbortController pour cette requête
    currentAbortController = new AbortController();
    const signal = currentAbortController.signal;

    // Charger depuis l'API
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

      // Stocker les stations
      currentStations.value = response.data.stations;
      currentTotal.value = response.data.total;

      // Initialiser les stations visibles avec les 40 premières
      visibleStations.value = response.data.stations.slice(0, 40);

      console.log(`✅ Loaded ${response.data.stations.length} stations (total: ${response.data.total})`);
      return true;
    } catch (error) {
      // Si la requête a été annulée, ne pas logger comme erreur
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
   * Charge plus de stations (pagination locale avec accumulation progressive)
   */
  function loadMore() {
    const increment = 40;
    const currentVisible = visibleStations.value.length;
    const maxAvailable = currentStations.value.length;

    // Calculer combien on peut ajouter
    const newLimit = Math.min(currentVisible + increment, maxAvailable);

    // Ajouter les nouvelles stations à la liste visible
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
      // Trouver l'objet station complet
      let station = null;

      // Chercher dans visibleStations
      station = visibleStations.value.find(s => s.id === stationId);

      // Si pas trouvée, chercher dans currentStation
      if (!station && currentStation.value?.id === stationId) {
        station = currentStation.value;
      }

      // Si pas trouvée, chercher dans favoriteStations
      if (!station) {
        station = favoriteStations.value.find(s => s.id === stationId);
      }

      // Si pas trouvée, chercher dans currentStations
      if (!station) {
        station = currentStations.value.find(s => s.id === stationId);
      }

      // Envoyer la station complète (ou juste l'ID si non trouvée)
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
    // Chercher la station
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
        // Retirer de visibleStations
        visibleStations.value = visibleStations.value.filter(s => s.id !== stationId);

        // Retirer de currentStations
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
     * Ajoute une station personnalisée avec upload d'image
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
        console.log('📻 Station personnalisée ajoutée:', newStation);
        return { success: true, station: newStation };
      } else {
        return { success: false, error: response.data.error || 'Échec ajout station' };
      }
    } catch (error) {
      console.error('Error adding custom station:', error);
      const errorMessage = error.response?.data?.detail || error.message || 'Erreur inconnue';
      return { success: false, error: errorMessage };
    }
  }

  async function removeCustomStation(stationId) {
    try {
      const response = await axios.post('/api/radio/custom/remove', { station_id: stationId });

      if (response.data.success) {
        console.log('📻 Station personnalisée supprimée:', stationId);

        // Retirer de visibleStations et currentStations
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
     * Supprime l'image importée d'une station
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
        console.log('🖼️ Image supprimée:', stationId);

        // Mettre à jour dans visibleStations
        const visibleIndex = visibleStations.value.findIndex(s => s.id === stationId);
        if (visibleIndex !== -1) {
          visibleStations.value[visibleIndex] = updatedStation;
        }

        // Mettre à jour dans currentStations
        const currentIndex = currentStations.value.findIndex(s => s.id === stationId);
        if (currentIndex !== -1) {
          currentStations.value[currentIndex] = updatedStation;
        }

        return { success: true, station: updatedStation };
      } else {
        return { success: false, error: response.data.error || 'Échec suppression image' };
      }
    } catch (error) {
      console.error('Error removing station image:', error);
      const errorMessage = error.response?.data?.detail || error.message || 'Erreur inconnue';
      return { success: false, error: errorMessage };
    }
  }

  function updateFromWebSocket(metadata) {
    // Mise à jour depuis le WebSocket (via unifiedAudioStore)
    if (metadata.station_id) {
      // Chercher la station dans les stations actuelles
      let station = currentStations.value.find(s => s.id === metadata.station_id);

      // Sinon, chercher dans les favoris
      if (!station) {
        station = favoriteStations.value.find(s => s.id === metadata.station_id);
      }

      if (station) {
        currentStation.value = station;
      } else {
        // Station pas encore chargée, créer un objet minimal
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
      // Pas de station en cours
      currentStation.value = null;
    }
  }

  async function handleFavoriteEvent(stationId, isFavorite) {
    // Synchroniser le statut favori depuis le backend
    console.log(`🔄 Syncing favorite status: ${stationId} = ${isFavorite}`);

    // Mettre à jour dans currentStations
    const currentStationObj = currentStations.value.find(s => s.id === stationId);
    if (currentStationObj) {
      currentStationObj.is_favorite = isFavorite;
    }

    // Mettre à jour dans visibleStations
    const visibleStation = visibleStations.value.find(s => s.id === stationId);
    if (visibleStation) {
      visibleStation.is_favorite = isFavorite;
    }

    // Mettre à jour dans favoriteStations
    const favoriteStation = favoriteStations.value.find(s => s.id === stationId);
    if (favoriteStation) {
      favoriteStation.is_favorite = isFavorite;
    }

    // Si ajout aux favoris, recharger les favoris
    if (isFavorite && !favoriteStation) {
      console.log('📻 New favorite added, reloading favorites');
      await loadStations(true);
    }

    // Mettre à jour currentStation si c'est celle-ci
    if (currentStation.value?.id === stationId) {
      currentStation.value.is_favorite = isFavorite;
    }
  }

  return {
    // État
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
