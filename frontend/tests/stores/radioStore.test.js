// frontend/tests/stores/radioStore.test.js
/**
 * radioStore owns the derived view of the radio source: what "the current
 * station" is (WS metadata enriched by locally-edited favorites), what the
 * recognised track is, progressive rendering of results, the top-stations
 * cache and the network-error retry policy.
 *
 * The pass-through actions (play/stop/favorite) are covered only where the
 * store decides something — the payload enrichment and the local list pruning.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { useRadioStore } from '@/stores/radioStore';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { apiCall } from '@/services/apiCall';
import { resetApiCallMock, ok, fail } from '../helpers/apiCallMock';

vi.mock('@/services/apiCall', () => import('../helpers/apiCallMock'));

const STATION = (id, extra = {}) => ({
  id,
  name: `Station ${id}`,
  url: `https://stream.example/${id}`,
  country: 'France',
  genre: 'jazz',
  bitrate: 128,
  codec: 'MP3',
  ...extra,
});

/**
 * The store keeps `searchResults` and the raw `favoriteStations` private —
 * publicly they surface as `displayedStations` and the sorted `favoriteStations`
 * computed. Seed them the way the app does: through loadStations().
 */
async function seedSearchResults(store, stations) {
  apiCall.get.mockResolvedValueOnce(ok({ stations, total: stations.length }));
  await store.loadStations();
}

async function seedFavorites(store, stations) {
  apiCall.get.mockResolvedValueOnce(ok({ stations }));
  await store.loadStations(true);
}

/** Put the unified store into "radio is playing X" without touching the network. */
function playingRadio(metadata) {
  useUnifiedAudioStore().updateState({
    data: {
      full_state: {
        active_source: 'radio',
        source_state: 'active',
        transitioning: false,
        multiroom_enabled: false,
        equalizer_effects_enabled: false,
        metadata,
      },
    },
  });
}

describe('radioStore', () => {
  let store;

  beforeEach(() => {
    resetApiCallMock();
    store = useRadioStore();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  describe('currentStation', () => {
    it('is null when radio is not the active source', () => {
      useUnifiedAudioStore().updateState({
        data: {
          full_state: {
            active_source: 'spotify',
            source_state: 'active',
            transitioning: false,
            multiroom_enabled: false,
            equalizer_effects_enabled: false,
            metadata: { station_id: 's1', station_name: 'Leftover' },
          },
        },
      });

      expect(store.currentStation).toBeNull();
    });

    it('is null while radio plays but no station is identified yet', () => {
      playingRadio({ title: 'buffering' });

      expect(store.currentStation).toBeNull();
    });

    it('builds the station from WS metadata', () => {
      playingRadio({ station_id: 's1', station_name: 'FIP', country: 'France' });

      expect(store.currentStation).toMatchObject({
        id: 's1',
        name: 'FIP',
        country: 'France',
        is_favorite: false,
      });
    });

    it('prefers the local favorite record over WS metadata', async () => {
      // A rename done in the UI updates favoriteStations immediately; the
      // backend metadata still carries the old name until the next broadcast.
      await seedFavorites(store, [STATION('s1', { name: 'My renamed station' })]);
      playingRadio({ station_id: 's1', station_name: 'Stale name' });

      expect(store.currentStation.name).toBe('My renamed station');
      expect(store.currentStation.is_favorite).toBe(true);
    });
  });

  describe('trackInfo', () => {
    it('exposes the Shazam-recognised track while radio is active', () => {
      playingRadio({
        station_id: 's1',
        track_title: 'So What',
        track_artist: 'Miles Davis',
        track_artwork: 'https://art/1.jpg',
      });

      expect(store.trackInfo).toEqual({
        title: 'So What',
        artist: 'Miles Davis',
        artwork: 'https://art/1.jpg',
      });
    });

    it('is null when no track has been recognised', () => {
      playingRadio({ station_id: 's1' });

      expect(store.trackInfo).toBeNull();
    });

    it('drops the recognised track as soon as radio stops being active', () => {
      playingRadio({ station_id: 's1', track_title: 'So What' });
      expect(store.trackInfo).not.toBeNull();

      useUnifiedAudioStore().updateState({
        data: {
          full_state: {
            active_source: 'none',
            source_state: 'ready',
            transitioning: false,
            multiroom_enabled: false,
            equalizer_effects_enabled: false,
            metadata: { station_id: 's1', track_title: 'So What' },
          },
        },
      });

      expect(store.trackInfo).toBeNull();
    });
  });

  describe('progressive rendering', () => {
    beforeEach(async () => {
      await seedSearchResults(store, Array.from({ length: 100 }, (_, i) => STATION(`s${i}`)));
    });

    it('displays the first page and flags favorites', async () => {
      await seedFavorites(store, [STATION('s3')]);

      expect(store.displayedStations).toHaveLength(40);
      expect(store.hasMoreStations).toBe(true);
      expect(store.displayedStations.find(s => s.id === 's3').is_favorite).toBe(true);
      expect(store.displayedStations.find(s => s.id === 's4').is_favorite).toBe(false);
    });

    it('loadMore extends the page and stops at the result count', () => {
      store.loadMore();
      expect(store.displayedStations).toHaveLength(80);

      store.loadMore();
      expect(store.displayedStations).toHaveLength(100);
      expect(store.hasMoreStations).toBe(false);

      store.loadMore();
      expect(store.displayedStations).toHaveLength(100);
    });
  });

  it('exposes favorites sorted by name, each marked favorite', async () => {
    await seedFavorites(store, [
      STATION('b', { name: 'Zeta' }),
      STATION('a', { name: 'Alpha' }),
    ]);

    expect(store.favoriteStations.map(s => s.name)).toEqual(['Alpha', 'Zeta']);
    expect(store.favoriteStations.every(s => s.is_favorite)).toBe(true);
  });

  describe('loadStations', () => {
    it('stores results and clears the loading flag', async () => {
      apiCall.get.mockResolvedValueOnce(ok({ stations: [STATION('s1')], total: 1 }));

      const promise = store.loadStations();
      expect(store.loading).toBe(true);

      await promise;
      expect(store.loading).toBe(false);
      expect(store.hasError).toBe(false);
      expect(store.displayedStations).toHaveLength(1);
    });

    it('serves the unfiltered top-stations list from cache on the second call', async () => {
      apiCall.get.mockResolvedValueOnce(ok({ stations: [STATION('s1')], total: 1 }));
      await store.loadStations();

      await store.loadStations();

      expect(apiCall.get).toHaveBeenCalledTimes(1);
      expect(store.displayedStations).toHaveLength(1);
    });

    it('refetches once the top-stations cache has expired', async () => {
      vi.useFakeTimers();
      apiCall.get.mockResolvedValue(ok({ stations: [STATION('s1')], total: 1 }));

      await store.loadStations();
      vi.advanceTimersByTime(3 * 60 * 1000 + 1);
      await store.loadStations();

      expect(apiCall.get).toHaveBeenCalledTimes(2);
    });

    it('bypasses the cache as soon as a filter is active', async () => {
      apiCall.get.mockResolvedValue(ok({ stations: [STATION('s1')], total: 1 }));
      await store.loadStations();

      store.searchQuery = 'jazz';
      await store.loadStations();

      expect(apiCall.get).toHaveBeenCalledTimes(2);
      expect(apiCall.get).toHaveBeenLastCalledWith(
        '/api/radio/stations',
        expect.objectContaining({ params: expect.objectContaining({ query: 'jazz' }) }),
      );
    });

    it('flags the search unavailable when the directory reports api_error', async () => {
      apiCall.get.mockResolvedValueOnce(ok({ api_error: true, stations: [], total: 0 }));

      const result = await store.loadStations();

      expect(result).toBe(false);
      expect(store.searchUnavailable).toBe(true);
      expect(store.hasError).toBe(true);
    });

    it('flags the search unavailable when the request fails at TCP level (status null)', async () => {
      apiCall.get.mockResolvedValueOnce(fail('Network Error', null));

      await store.loadStations();

      expect(store.searchUnavailable).toBe(true);
      expect(store.hasError).toBe(true);
    });

    it('leaves the search available on an HTTP error status', async () => {
      apiCall.get.mockResolvedValueOnce(fail('Bad request', 400));

      await store.loadStations();

      expect(store.hasError).toBe(true);
      expect(store.searchUnavailable).toBe(false);
    });

    it('stays silent when the request was cancelled by a newer search', async () => {
      // apiCall reports cancellation as { ok: false, error: null }.
      apiCall.get.mockResolvedValueOnce({ ok: false, data: null, error: null });

      const result = await store.loadStations();

      expect(result).toBe(false);
      expect(store.hasError).toBe(false);
      expect(store.searchUnavailable).toBe(false);
    });

    it('loads favorites into their own list when favoritesOnly is set', async () => {
      apiCall.get.mockResolvedValueOnce(ok({ stations: [STATION('s1')], total: 1 }));

      await store.loadStations(true);

      expect(apiCall.get).toHaveBeenCalledWith(
        '/api/radio/stations',
        expect.objectContaining({ params: { favorites_only: true } }),
      );
      expect(store.favoriteStations).toHaveLength(1);
      expect(store.displayedStations).toHaveLength(0);
    });
  });

  describe('preloadFavorites', () => {
    it('fetches once and marks favorites initialised', async () => {
      apiCall.get.mockResolvedValue(ok({ stations: [STATION('s1')] }));

      await store.preloadFavorites();
      await store.preloadFavorites();

      expect(apiCall.get).toHaveBeenCalledTimes(1);
      expect(store.favoritesInitialized).toBe(true);
    });

    it('refetches when forced, as the resync path does', async () => {
      apiCall.get.mockResolvedValue(ok({ stations: [STATION('s1')] }));
      await store.preloadFavorites();

      await store.preloadFavorites({ force: true });

      expect(apiCall.get).toHaveBeenCalledTimes(2);
      // Favorites stay initialised during the refetch so the view keeps its data.
      expect(store.favoritesInitialized).toBe(true);
    });
  });

  describe('play / favorite payloads', () => {
    it('sends the full station object when the store already knows it', async () => {
      // Lets the backend play a station that is not in its own catalog
      // (custom or search-only result) without a second lookup.
      const station = STATION('s1');
      await seedSearchResults(store, [station]);
      apiCall.post.mockResolvedValueOnce(ok({ success: true }));

      await store.playStation('s1');

      expect(apiCall.post).toHaveBeenCalledWith(
        '/api/radio/play',
        { station_id: 's1', station },
        expect.anything(),
      );
    });

    it('falls back to the bare id for an unknown station', async () => {
      apiCall.post.mockResolvedValueOnce(ok({ success: true }));

      await store.playStation('unknown');

      expect(apiCall.post).toHaveBeenCalledWith(
        '/api/radio/play',
        { station_id: 'unknown' },
        expect.anything(),
      );
    });

    it('reports failure when the backend answers success: false', async () => {
      apiCall.post.mockResolvedValueOnce(ok({ success: false }));

      expect(await store.playStation('s1')).toBe(false);
    });

    it('toggleFavorite removes a station that is already a favorite', async () => {
      await seedFavorites(store, [STATION('s1')]);
      apiCall.delete.mockResolvedValueOnce(ok({ success: true }));

      await store.toggleFavorite('s1');

      expect(apiCall.delete).toHaveBeenCalledWith('/api/radio/favorites/s1', expect.anything());
      expect(apiCall.post).not.toHaveBeenCalled();
    });

    it('toggleFavorite adds a station that is not yet a favorite', async () => {
      apiCall.post.mockResolvedValueOnce(ok({ success: true }));

      await store.toggleFavorite('s1');

      expect(apiCall.post).toHaveBeenCalledWith(
        '/api/radio/favorites/add',
        { station_id: 's1' },
        expect.anything(),
      );
      expect(apiCall.delete).not.toHaveBeenCalled();
    });
  });

  describe('custom stations', () => {
    it('prunes a removed custom station from the local results', async () => {
      await seedSearchResults(store, [STATION('custom1'), STATION('s2')]);
      apiCall.delete.mockResolvedValueOnce(ok({ success: true }));

      const removed = await store.removeCustomStation('custom1');

      expect(removed).toBe(true);
      expect(store.displayedStations.map(s => s.id)).toEqual(['s2']);
    });

    it('keeps the local results intact when the removal fails', async () => {
      await seedSearchResults(store, [STATION('custom1')]);
      apiCall.delete.mockResolvedValueOnce(fail('Not found', 404));

      expect(await store.removeCustomStation('custom1')).toBe(false);
      expect(store.displayedStations).toHaveLength(1);
    });

    it('surfaces the backend error detail when adding fails', async () => {
      apiCall.post.mockResolvedValueOnce(ok({ success: false, error: 'Invalid URL' }));

      const result = await store.addCustomStation({ name: 'Bad', url: 'invalid' });

      expect(result).toEqual({ success: false, error: 'Invalid URL' });
    });

    it('sends the creation as multipart form data', async () => {
      apiCall.post.mockResolvedValueOnce(ok({ success: true, station: STATION('custom1') }));

      await store.addCustomStation({ name: 'My Station', url: 'https://stream.example' });

      const [url, body, options] = apiCall.post.mock.calls[0];
      expect(url).toBe('/api/radio/custom/add');
      expect(body).toBeInstanceOf(FormData);
      expect(body.get('name')).toBe('My Station');
      // Defaults to enabled — the backend expects the field on every creation.
      expect(body.get('shazam_enabled')).toBe('true');
      expect(options.headers).toEqual({ 'Content-Type': 'multipart/form-data' });
    });
  });

  describe('handleMetadataModified', () => {
    it('updates the station in both the favorites and the search list', async () => {
      await seedFavorites(store, [STATION('s1')]);
      await seedSearchResults(store, [STATION('s1'), STATION('s2')]);

      store.handleMetadataModified({ ...STATION('s1'), name: 'Renamed' });

      expect(store.favoriteStations[0].name).toBe('Renamed');
      expect(store.favoriteStations[0].is_favorite).toBe(true);
      expect(store.displayedStations[0].name).toBe('Renamed');
      expect(store.displayedStations[1].name).toBe('Station s2');
    });

    it('leaves the custom dict alone until the settings view has asked for it', () => {
      // Editing a station is what creates a custom entry, so the dict must
      // refresh — but only once something is displaying it. Refetching for a
      // view that was never opened is a request nobody reads.
      store.handleMetadataModified(STATION('s1'));

      expect(apiCall.get).not.toHaveBeenCalled();
    });

    it('refreshes the custom dict once it has been loaded', async () => {
      apiCall.get.mockResolvedValueOnce(ok({ custom1: STATION('custom1') }));
      await store.loadRadioSettingsData();
      apiCall.get.mockResolvedValueOnce(ok({ custom1: { ...STATION('custom1'), name: 'Renamed' } }));

      store.handleMetadataModified({ ...STATION('custom1'), name: 'Renamed' });
      await vi.waitFor(() => expect(store.customStations.custom1.name).toBe('Renamed'));

      // The dict is refetched, not patched: the event carries the station, not
      // whether it has just become a custom entry.
      expect(apiCall.get).toHaveBeenLastCalledWith('/api/radio/custom', expect.anything());
    });
  });
});
