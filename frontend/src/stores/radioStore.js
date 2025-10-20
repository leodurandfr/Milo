// frontend/src/stores/radioStore.js
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import axios from 'axios';

export const useRadioStore = defineStore('radio', () => {
  // === ÉTAT ===
  const stations = ref([]);
  const currentStation = ref(null);
  // SUPPRIMÉ: isPlaying local - utiliser unifiedAudioStore.metadata.is_playing à la place
  // SUPPRIMÉ: loadingStationId local - utiliser unifiedAudioStore.metadata.buffering à la place
  const loading = ref(false);
  const searchQuery = ref('');
  const countryFilter = ref('');
  const genreFilter = ref('');

  // === GETTERS ===
  const filteredStations = computed(() => {
    let result = stations.value;

    if (searchQuery.value) {
      const query = searchQuery.value.toLowerCase();
      result = result.filter(s =>
        s.name.toLowerCase().includes(query) ||
        s.genre.toLowerCase().includes(query)
      );
    }

    if (countryFilter.value) {
      const country = countryFilter.value.toLowerCase();
      result = result.filter(s => s.country.toLowerCase().includes(country));
    }

    if (genreFilter.value) {
      const genre = genreFilter.value.toLowerCase();
      result = result.filter(s => s.genre.toLowerCase().includes(genre));
    }

    return result;
  });

  const favoriteStations = computed(() => {
    return stations.value
      .filter(s => s.is_favorite)
      .sort((a, b) => a.name.localeCompare(b.name));
  });

  // === ACTIONS ===
  async function loadStations(favoritesOnly = false) {
    loading.value = true;
    try {
      const params = {
        limit: 10000, // Limite très élevée pour récupérer toutes les stations d'un pays
        favorites_only: favoritesOnly
      };

      if (searchQuery.value) params.query = searchQuery.value;
      if (countryFilter.value) params.country = countryFilter.value;
      if (genreFilter.value) params.genre = genreFilter.value;

      const response = await axios.get('/api/radio/stations', { params });
      stations.value = response.data;
      return true;
    } catch (error) {
      console.error('Error loading stations:', error);
      return false;
    } finally {
      loading.value = false;
    }
  }


  async function playStation(stationId) {
    try {
      // SIMPLIFIÉ: Pas d'optimistic update - faire confiance au backend
      // L'état de buffering sera synchronisé via WebSocket (metadata.buffering)
      const response = await axios.post('/api/radio/play', { station_id: stationId });
      return response.data.success;
    } catch (error) {
      console.error('Error playing station:', error);
      return false;
    }
  }

  async function stopPlayback() {
    try {
      // SIMPLIFIÉ: Pas d'optimistic update - faire confiance au backend
      // L'état sera synchronisé via WebSocket
      const response = await axios.post('/api/radio/stop');
      return response.data.success;
    } catch (error) {
      console.error('Error stopping playback:', error);
      return false;
    }
  }

  async function addFavorite(stationId) {
    try {
      // SIMPLIFIÉ: L'état sera synchronisé via WebSocket
      const response = await axios.post('/api/radio/favorites/add', { station_id: stationId });
      return response.data.success;
    } catch (error) {
      console.error('Error adding favorite:', error);
      return false;
    }
  }

  async function removeFavorite(stationId) {
    try {
      // SIMPLIFIÉ: L'état sera synchronisé via WebSocket
      const response = await axios.post('/api/radio/favorites/remove', { station_id: stationId });
      return response.data.success;
    } catch (error) {
      console.error('Error removing favorite:', error);
      return false;
    }
  }

  async function toggleFavorite(stationId) {
    // Chercher d'abord dans la liste des stations
    let station = stations.value.find(s => s.id === stationId);

    // Si pas trouvée, utiliser currentStation si c'est la bonne
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
        // Retirer de la liste
        stations.value = stations.value.filter(s => s.id !== stationId);
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
     *
     * @param {Object} stationData - Données de la station
     * @param {string} stationData.name - Nom de la station
     * @param {string} stationData.url - URL du flux audio
     * @param {string} stationData.country - Pays (défaut: "France")
     * @param {string} stationData.genre - Genre (défaut: "Variety")
     * @param {File} stationData.image - Fichier image (optionnel)
     * @param {number} stationData.bitrate - Bitrate (défaut: 128)
     * @param {string} stationData.codec - Codec (défaut: "MP3")
     * @returns {Promise<{success: boolean, station?: Object, error?: string}>}
     */
    try {
      // Créer un FormData pour l'upload multipart/form-data
      const formData = new FormData();
      formData.append('name', stationData.name);
      formData.append('url', stationData.url);
      formData.append('country', stationData.country || 'France');
      formData.append('genre', stationData.genre || 'Variety');
      formData.append('bitrate', stationData.bitrate || 128);
      formData.append('codec', stationData.codec || 'MP3');

      // Ajouter l'image si fournie
      if (stationData.image) {
        formData.append('image', stationData.image);
      }

      const response = await axios.post('/api/radio/custom/add', formData, {
        headers: {
          'Content-Type': 'multipart/form-data'
        }
      });

      if (response.data.success) {
        // Ajouter la nouvelle station à la liste
        const newStation = response.data.station;
        stations.value.unshift(newStation); // Ajouter au début
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
        // Retirer de la liste
        stations.value = stations.value.filter(s => s.id !== stationId);
        console.log('📻 Station personnalisée supprimée:', stationId);
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
     *
     * @param {string} stationId - ID de la station
     * @returns {Promise<{success: boolean, station?: Object, error?: string}>}
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
        // Mettre à jour la station dans la liste
        const updatedStation = response.data.station;
        const index = stations.value.findIndex(s => s.id === stationId);
        if (index !== -1) {
          stations.value[index] = updatedStation;
        }

        console.log('🖼️ Image supprimée:', stationId);
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
    // SIMPLIFIÉ: Synchronisation directe depuis le backend (source de vérité)
    // Plus d'optimistic updates à gérer

    // Mise à jour depuis le WebSocket (via unifiedAudioStore)
    if (metadata.station_id) {
      const station = stations.value.find(s => s.id === metadata.station_id);
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
      // Pas de station en cours (plugin en mode READY)
      currentStation.value = null;
    }

    // NOTE: isPlaying est maintenant géré par unifiedAudioStore.metadata.is_playing
    // NOTE: buffering est maintenant géré par unifiedAudioStore.metadata.buffering
    // Ce store ne maintient plus d'état local pour ces propriétés
  }

  async function handleFavoriteEvent(stationId, isFavorite) {
    // Synchroniser le statut favori depuis le backend (source de vérité)
    console.log(`🔄 Syncing favorite status: ${stationId} = ${isFavorite}`);

    // Mettre à jour dans la liste des stations
    const station = stations.value.find(s => s.id === stationId);
    if (station) {
      station.is_favorite = isFavorite;
    } else if (isFavorite) {
      // Si la station n'est pas dans la liste et qu'elle vient d'être ajoutée aux favoris
      // (cas d'un ajout depuis un autre device), recharger les stations en mode favoris
      console.log('📻 Station not found locally, reloading favorites from backend');
      await loadStations(true);
    }

    // Mettre à jour currentStation si c'est celle-ci
    if (currentStation.value?.id === stationId) {
      currentStation.value.is_favorite = isFavorite;
    }

    // Note: favoriteStations getter se met à jour automatiquement quand stations change
  }

  return {
    // État
    stations,
    currentStation,
    loading,
    searchQuery,
    countryFilter,
    genreFilter,

    // Getters (valeurs calculées)
    filteredStations,
    favoriteStations,

    // Actions
    loadStations,
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
