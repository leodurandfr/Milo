// frontend/tests/stores/radioStore.test.js
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useRadioStore } from '@/stores/radioStore';
import axios from 'axios';

// Mock axios
vi.mock('axios');

describe('radioStore', () => {
  let store;

  beforeEach(() => {
    store = useRadioStore();
    vi.clearAllMocks();
    localStorage.getItem.mockReturnValue(null);
  });

  describe('initial state', () => {
    it('should have null current station', () => {
      expect(store.currentStation).toBeNull();
    });

    it('should not be loading initially', () => {
      expect(store.loading).toBe(false);
    });

    it('should not have error initially', () => {
      expect(store.hasError).toBe(false);
    });

    it('should have empty search query', () => {
      expect(store.searchQuery).toBe('');
    });

    it('should have empty filters', () => {
      expect(store.countryFilter).toBe('');
      expect(store.genreFilter).toBe('');
    });

    it('should have empty displayed stations', () => {
      expect(store.displayedStations).toEqual([]);
    });
  });

  describe('favorites', () => {
    describe('addFavorite', () => {
      it('should call API to add favorite', async () => {
        axios.post.mockResolvedValueOnce({ data: { success: true } });

        await store.addFavorite('station1');

        expect(axios.post).toHaveBeenCalledWith('/api/radio/favorites/add', expect.objectContaining({
          station_id: 'station1'
        }));
      });
    });

    describe('removeFavorite', () => {
      it('should call API to remove favorite', async () => {
        axios.delete.mockResolvedValueOnce({ data: { success: true } });

        await store.removeFavorite('station1');

        expect(axios.delete).toHaveBeenCalledWith('/api/radio/favorites/station1');
      });
    });

    describe('toggleFavorite', () => {
      it('should add favorite for unknown station', async () => {
        axios.post.mockResolvedValueOnce({ data: { success: true } });

        const result = await store.toggleFavorite('unknown-station');

        expect(axios.post).toHaveBeenCalledWith('/api/radio/favorites/add', expect.objectContaining({
          station_id: 'unknown-station'
        }));
        expect(result).toBe(true);
      });
    });
  });

  describe('playback', () => {
    describe('playStation', () => {
      it('should call play API with station ID', async () => {
        axios.post.mockResolvedValueOnce({ data: { success: true } });

        await store.playStation('station1');

        expect(axios.post).toHaveBeenCalledWith('/api/radio/play', { station_id: 'station1' });
      });

      it('should return true on success', async () => {
        axios.post.mockResolvedValueOnce({ data: { success: true } });

        const result = await store.playStation('station1');

        expect(result).toBe(true);
      });

      it('should return false on error', async () => {
        axios.post.mockRejectedValueOnce(new Error('Network error'));

        const result = await store.playStation('station1');

        expect(result).toBe(false);
      });
    });

    describe('stopPlayback', () => {
      it('should call stop API', async () => {
        axios.post.mockResolvedValueOnce({ data: { success: true } });

        await store.stopPlayback();

        expect(axios.post).toHaveBeenCalledWith('/api/radio/stop');
      });
    });
  });

  describe('loadStations', () => {
    it('should load stations from API', async () => {
      const mockStations = [
        { id: 's1', name: 'Station 1' },
        { id: 's2', name: 'Station 2' }
      ];
      axios.get.mockResolvedValueOnce({
        data: { stations: mockStations, total: 2 }
      });

      await store.loadStations();

      expect(axios.get).toHaveBeenCalled();
    });

    it('should set loading state during fetch', async () => {
      let capturedLoading;
      axios.get.mockImplementationOnce(() => {
        capturedLoading = store.loading;
        return Promise.resolve({ data: { stations: [], total: 0 } });
      });

      await store.loadStations();

      expect(capturedLoading).toBe(true);
      expect(store.loading).toBe(false);
    });

    it('should handle API errors gracefully', async () => {
      axios.get.mockRejectedValueOnce(new Error('Network error'));

      await store.loadStations();

      expect(store.hasError).toBe(true);
    });
  });

  describe('custom stations', () => {
    describe('addCustomStation', () => {
      it('should call API to add custom station', async () => {
        axios.post.mockResolvedValueOnce({
          data: {
            success: true,
            station: { id: 'custom1', name: 'My Station', url: 'https://stream.example.com' }
          }
        });

        const result = await store.addCustomStation({
          name: 'My Station',
          url: 'https://stream.example.com'
        });

        expect(axios.post).toHaveBeenCalledWith(
          '/api/radio/custom/add',
          expect.any(FormData),
          expect.objectContaining({
            headers: { 'Content-Type': 'multipart/form-data' }
          })
        );
        expect(result.success).toBe(true);
      });

      it('should return error on failure', async () => {
        axios.post.mockResolvedValueOnce({
          data: { success: false, error: 'Invalid URL' }
        });

        const result = await store.addCustomStation({
          name: 'Bad',
          url: 'invalid'
        });

        expect(result.success).toBe(false);
        expect(result.error).toBe('Invalid URL');
      });
    });

    describe('removeCustomStation', () => {
      it('should call API to remove custom station', async () => {
        axios.delete.mockResolvedValueOnce({ data: { success: true } });

        const result = await store.removeCustomStation('custom1');

        expect(axios.delete).toHaveBeenCalledWith('/api/radio/custom/custom1');
        expect(result).toBe(true);
      });
    });
  });

});
