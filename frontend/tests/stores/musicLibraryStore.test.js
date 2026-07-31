// frontend/tests/stores/musicLibraryStore.test.js
/**
 * musicLibraryStore scopes every catalog read to ONE storage space (a Navidrome
 * library id). Three things can break that and nothing else would notice:
 * the selection landing on a storage Navidrome cannot serve, a cached list
 * surviving a switch (the previous key's albums shown under the new one), and a
 * selection left pointing at a key that has been unplugged.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { nextTick } from 'vue';
import { useMusicLibraryStore } from '@/stores/musicLibraryStore';
import { apiCall } from '@/services/apiCall';
import { resetApiCallMock, ok } from '../helpers/apiCallMock';

vi.mock('@/services/apiCall', () => import('../helpers/apiCallMock'));

const USB = { kind: 'usb', id: '1234-ABCD', name: 'iPod', mountpoint: '/media/milo/iPod', mounted: true, library_id: 3 };
const NAS = { kind: 'share', id: 'nas-1', name: 'NAS-Leo', mountpoint: '/media/milo/nas-1', mounted: true, library_id: 2 };

/** The params of the last GET issued to a given path. */
const paramsOf = (path) =>
  apiCall.get.mock.calls.filter(([url]) => url.endsWith(path)).at(-1)?.[1]?.params;

describe('musicLibraryStore — storage scoping', () => {
  let store;

  beforeEach(() => {
    resetApiCallMock();
    store = useMusicLibraryStore();
  });

  it('selects the first browsable storage and ignores one with no library', async () => {
    const pending = { ...USB, id: 'pending', name: 'New key', library_id: null };
    apiCall.get.mockResolvedValueOnce(ok({ storages: [NAS, USB, pending] }));

    await store.loadStorages();
    await nextTick();

    expect(store.browsableStorages.map((s) => s.name)).toEqual(['NAS-Leo', 'iPod']);
    expect(store.activeLibraryId).toBe(NAS.library_id);
  });

  it('sends the active library id with every catalog read', async () => {
    apiCall.get.mockResolvedValueOnce(ok({ storages: [NAS, USB] }));
    await store.loadStorages();
    await nextTick();

    apiCall.get.mockResolvedValue(ok({ albums: [], index: [], genres: [], artists: [], songs: [] }));
    await store.loadAlbums();
    await store.loadArtists();
    await store.loadGenres();

    expect(paramsOf('/albums').library_id).toBe(NAS.library_id);
    expect(paramsOf('/artists').library_id).toBe(NAS.library_id);
    expect(paramsOf('/genres').library_id).toBe(NAS.library_id);
  });

  it('drops the cached catalog on a switch and refetches what was loaded', async () => {
    apiCall.get.mockResolvedValueOnce(ok({ storages: [NAS, USB] }));
    await store.loadStorages();
    await nextTick();

    apiCall.get.mockResolvedValueOnce(ok({ albums: [{ id: 'a1', name: 'On the NAS' }] }));
    await store.loadAlbums();
    expect(store.albums).toHaveLength(1);

    // Switching to the USB key: the NAS albums must not survive the switch, and
    // the refetch must carry the new library id.
    apiCall.get.mockResolvedValueOnce(ok({ albums: [{ id: 'a2', name: 'On the key' }] }));
    store.activeLibraryId = USB.library_id;
    await nextTick();
    await Promise.resolve();

    expect(paramsOf('/albums').library_id).toBe(USB.library_id);
    expect(store.albums.map((a) => a.id)).toEqual(['a2']);
  });

  it('drops an in-flight page that lands after the user switched storage', async () => {
    apiCall.get.mockResolvedValueOnce(ok({ storages: [NAS, USB] }));
    await store.loadStorages();
    await nextTick();
    // A full page, so the grid still believes there is more to fetch.
    const firstPage = Array.from({ length: 40 }, (_, i) => ({ id: `nas-${i}` }));
    apiCall.get.mockResolvedValueOnce(ok({ albums: firstPage }));
    await store.loadAlbums();
    expect(store.albumsHasMore).toBe(true);

    // Infinite scroll asks for the NAS's next page, and the user taps the USB
    // key before it answers. The late page must not be appended to the key's
    // grid — the two lists share one ref.
    let releasePage;
    apiCall.get.mockReturnValueOnce(new Promise((resolve) => {
      releasePage = () => resolve(ok({ albums: [{ id: 'nas-2' }] }));
    }));
    const inFlight = store.loadMoreAlbums();

    apiCall.get.mockResolvedValueOnce(ok({ albums: [{ id: 'usb-1' }] }));
    store.activeLibraryId = USB.library_id;
    await nextTick();
    await Promise.resolve();

    releasePage();
    await inFlight;

    expect(store.albums.map((a) => a.id)).toEqual(['usb-1']);
  });

  it('falls back to a remaining storage when the selected key is unplugged', async () => {
    apiCall.get.mockResolvedValueOnce(ok({ storages: [NAS, USB] }));
    await store.loadStorages();
    await nextTick();
    store.activeLibraryId = USB.library_id;
    await nextTick();

    apiCall.get.mockResolvedValueOnce(ok({ storages: [NAS] }));
    await store.loadStorages({ force: true });
    await nextTick();

    expect(store.activeLibraryId).toBe(NAS.library_id);
  });
});

/**
 * getArtists returns the whole A–Z index in one call, so the Artists tab is the
 * one list nothing paces: mounting every row in a single tick froze the UI for
 * ~1s at 550 artists. The window below is what keeps that bounded — if it stops
 * truncating, or stops being extendable, the freeze comes back silently (the
 * list still looks right, it just costs a second to appear).
 */
describe('musicLibraryStore — artists render window', () => {
  let store;

  // 3 buckets, 30 + 25 + 5 artists: the first two straddle one 40-row chunk, so
  // a per-bucket window would leak where a per-artist one truncates.
  const INDEX = [
    { name: 'A', artist: Array.from({ length: 30 }, (_, i) => ({ id: `a${i}`, name: `A${i}` })) },
    { name: 'B', artist: Array.from({ length: 25 }, (_, i) => ({ id: `b${i}`, name: `B${i}` })) },
    { name: 'C', artist: Array.from({ length: 5 }, (_, i) => ({ id: `c${i}`, name: `C${i}` })) },
  ];
  const rendered = () => store.displayedArtistIndex.reduce((n, b) => n + b.artist.length, 0);

  beforeEach(async () => {
    resetApiCallMock();
    store = useMusicLibraryStore();
    apiCall.get.mockResolvedValueOnce(ok({ index: INDEX }));
    await store.loadArtists();
  });

  it('renders one chunk across bucket boundaries, not one bucket', () => {
    expect(rendered()).toBe(40);
    // The cut lands mid-B: bucket A whole, B truncated, C not mounted at all.
    expect(store.displayedArtistIndex.map((b) => [b.name, b.artist.length]))
      .toEqual([['A', 30], ['B', 10]]);
    expect(store.artistsHasMore).toBe(true);
  });

  it('widens to the full index and then stops', () => {
    store.renderMoreArtists();
    expect(rendered()).toBe(60);
    expect(store.displayedArtistIndex.at(-1).name).toBe('C');
    expect(store.artistsHasMore).toBe(false);

    // Already exhausted: a stray sentinel hit must not widen the window past the
    // index, or the next library's first chunk would mount unpaced.
    store.renderMoreArtists();
    expect(rendered()).toBe(60);
  });

  it('collapses back to one chunk when the index is refetched', async () => {
    store.renderMoreArtists();
    expect(store.artistsHasMore).toBe(false);

    apiCall.get.mockResolvedValueOnce(ok({ index: INDEX }));
    await store.loadArtists({ force: true });

    expect(rendered()).toBe(40);
  });
});
