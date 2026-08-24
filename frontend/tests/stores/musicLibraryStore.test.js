// frontend/tests/stores/musicLibraryStore.test.js
/**
 * musicLibraryStore scopes every catalog read to ONE storage space (a Navidrome
 * library id), unless the user merged them. What can break that, silently, is
 * the selection: landing on a storage Navidrome cannot serve, a cached list
 * surviving a switch (the previous key's albums shown under the new one), a
 * dead library id outliving the storage it named — and the two opposite cases
 * an unplug and a deletion must NOT be confused with each other.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { nextTick } from 'vue';
import { useMusicLibraryStore } from '@/stores/musicLibraryStore';
import { useSettingsStore } from '@/stores/settingsStore';
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

  it('falls back to a remaining storage when the selected one leaves the list', async () => {
    // Leaving the list is a deletion (share removed, key forgotten) — distinct
    // from an unplug, which keeps the entry. Nothing scopes to it any more, so
    // the selection has to move or every read would carry a dead library id.
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

  it('keeps the selection on a key that was unplugged, and reports it', async () => {
    // The opposite of the case above, and the reason the two are told apart:
    // switching away on an unplug would swap the view out with no explanation.
    // Holding the selection is what lets LibraryHome say the key was removed —
    // so a silent fallback here would delete that message from the UI.
    apiCall.get.mockResolvedValueOnce(ok({ storages: [NAS, USB] }));
    await store.loadStorages();
    await nextTick();
    store.activeLibraryId = USB.library_id;
    await nextTick();

    store.handleStoragesEvent({
      data: { storages: [NAS, { ...USB, mounted: false }], scanning: false },
    });
    await nextTick();

    expect(store.activeLibraryId).toBe(USB.library_id);
    expect(store.disconnectedStorage?.id).toBe(USB.id);
    // …and it is no longer offered as somewhere to browse.
    expect(store.browsableStorages.map((s) => s.id)).toEqual([NAS.id]);
  });

  it('keeps an absent space in the three views the settings screen reads', async () => {
    // `browsableStorages` is the only one that may filter on `mounted`. The
    // settings screen is where an absent space is still administered, and each
    // of the other three breaks differently if it starts filtering too:
    //   storages   → hides the `separate_storages` toggle (`length > 1` IS the
    //                switch), removing the only way out of merged mode;
    //   usbDevices → an unplugged key can no longer be renamed or forgotten,
    //                and forgetting is the only way to drop its index rows;
    //   shares     → the NAS row survives but its track count reads zero.
    apiCall.get.mockResolvedValueOnce(ok({
      storages: [{ ...NAS, mounted: false, track_count: 2419 }, { ...USB, mounted: false }],
    }));
    await store.loadStorages();
    await nextTick();

    // The share config carries neither figure: both are folded in from the
    // storage push, which is what greys a row out with no refetch.
    apiCall.get.mockResolvedValueOnce(ok({
      shares: [{ id: NAS.id, type: 'cifs', host: '192.168.1.20', name: NAS.name }],
    }));
    await store.loadShares();

    expect(store.browsableStorages).toEqual([]);
    expect(store.storages.map((s) => s.id)).toEqual([NAS.id, USB.id]);
    expect(store.usbDevices.map((s) => s.id)).toEqual([USB.id]);
    expect(store.shares.map((s) => [s.id, s.mounted, s.track_count]))
      .toEqual([[NAS.id, false, 2419]]);
  });

  it('drops the scope entirely when storage spaces are merged', async () => {
    // separate_storages=false is the whole merged mode: every catalog read must
    // go out unscoped, or the user would still be looking at one storage space
    // with the picker hidden and no way back.
    const settings = useSettingsStore();
    settings.updateMusicLibrarySettings({ separate_storages: false });

    apiCall.get.mockResolvedValueOnce(ok({ storages: [NAS, USB] }));
    await store.loadStorages();
    await nextTick();

    apiCall.get.mockResolvedValue(ok({ albums: [], index: [], genres: [] }));
    await store.loadAlbums();

    expect(store.activeLibraryId).toBeNull();
    expect(paramsOf('/albums')).not.toHaveProperty('library_id');
  });

  it('reports the active storage track count, not the frozen global one', async () => {
    // Navidrome's global scan counter does not move until a scan ends, so the
    // per-storage total is the only figure that can show progress. Summing them
    // is what the merged view has instead.
    apiCall.get.mockResolvedValueOnce(ok({
      storages: [{ ...NAS, track_count: 2419 }, { ...USB, track_count: 10069 }],
      scanning: true,
    }));
    await store.loadStorages();
    await nextTick();

    expect(store.isScanning).toBe(true);
    expect(store.activeStorageTrackCount).toBe(2419);

    store.activeLibraryId = USB.library_id;
    await nextTick();
    expect(store.activeStorageTrackCount).toBe(10069);
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

/**
 * A mounted storage space whose tracks Navidrome has all flagged missing. The
 * catalog reads empty, but for the opposite reason to "no music here yet": the
 * files are present and the index disagrees. Told apart, the screen offers a
 * re-index; conflated, it tells someone whose NAS is mounted and full to go
 * connect a NAS — which is what it did for 16 hours after a scan ran against
 * the mountpoint before the share had finished mounting.
 */
describe('musicLibraryStore — a storage space the index has lost', () => {
  const indexed = (s, tracks) => ({ ...s, track_count: tracks, missing_count: 0 });
  const lost = (s, missing) => ({ ...s, track_count: 0, missing_count: missing });

  beforeEach(() => {
    resetApiCallMock();
  });

  it('names the state when every track of the browsed space is missing', async () => {
    const store = useMusicLibraryStore();
    apiCall.get.mockResolvedValueOnce(ok({ storages: [lost(NAS, 2419)] }));

    await store.loadStorages();
    await nextTick();

    expect(store.unindexedStorage?.name).toBe('NAS-Leo');
  });

  it('does not confuse it with a space that is simply empty', async () => {
    const store = useMusicLibraryStore();
    // Nothing indexed and nothing missing: a blank USB key. Offering a re-index
    // here would send someone chasing a scan that has nothing to find.
    apiCall.get.mockResolvedValueOnce(ok({ storages: [lost(USB, 0)] }));

    await store.loadStorages();
    await nextTick();

    expect(store.unindexedStorage).toBeNull();
  });

  it('does not confuse it with a space that is merely unplugged', async () => {
    const store = useMusicLibraryStore();
    // An unplugged key keeps its index and its counts; it is not browsable, and
    // it already has its own message. A rescan cannot repair what it cannot walk.
    apiCall.get.mockResolvedValueOnce(
      ok({ storages: [{ ...lost(USB, 10069), mounted: false }] })
    );

    await store.loadStorages();
    await nextTick();

    expect(store.unindexedStorage).toBeNull();
  });

  it('ignores a broken space the user is not looking at', async () => {
    const store = useMusicLibraryStore();
    apiCall.get.mockResolvedValueOnce(
      ok({ storages: [indexed(NAS, 2419), lost(USB, 10069)] })
    );

    await store.loadStorages();
    await nextTick();

    // Browsing the healthy NAS: its albums are there, so the empty-state message
    // belongs to the space on screen, not to whichever one is worst off.
    expect(store.activeLibraryId).toBe(NAS.library_id);
    expect(store.unindexedStorage).toBeNull();
  });
});

/**
 * Dropping a response that lands after a storage switch is correct; dropping the
 * loading flag with it is not. LibraryHome renders its skeletons while
 * `<list>Loading` is set and gates the infinite-scroll sentinel on the same
 * flag, and the switch watcher refetches only lists that had already *loaded* —
 * so a first load abandoned mid-flight leaves the tab on placeholders with
 * nothing left able to ask for the data.
 */
describe('musicLibraryStore — a loader that lands out of scope', () => {
  let store;

  beforeEach(async () => {
    resetApiCallMock();
    store = useMusicLibraryStore();
    apiCall.get.mockResolvedValueOnce(ok({ storages: [NAS, USB] }));
    await store.loadStorages();
    await nextTick();
  });

  /** Start a read, switch storage while it is in flight, then let it land. */
  async function landAfterSwitch(start, body, thenServes = null) {
    let release;
    apiCall.get.mockReturnValueOnce(
      new Promise((resolve) => { release = () => resolve(ok(body)); })
    );
    const inFlight = start();
    // Queued only now: what the switch watcher refetches must go behind the
    // response held open above, not in front of it.
    if (thenServes) apiCall.get.mockResolvedValueOnce(ok(thenServes));
    store.activeLibraryId = USB.library_id;
    await nextTick();
    release();
    await inFlight;
  }

  // The first load of a tab: `<list>Loaded` is still false, so the switch
  // watcher reloads nothing and this response is the only one in flight.
  it.each([
    ['loadAlbums', (s) => s.loadAlbums(), 'albumsLoading', { albums: [{ id: 'nas-1' }] }],
    ['loadArtists', (s) => s.loadArtists(), 'artistsLoading', { index: [{ name: 'A', artist: [] }] }],
    ['loadGenres', (s) => s.loadGenres(), 'genresLoading', { genres: [{ value: 'Jazz' }] }],
    ['loadPlaylists', (s) => s.loadPlaylists(), 'playlistsLoading', { playlists: [{ id: 'p1' }] }],
  ])('%s releases its loading flag', async (_name, start, flag, body) => {
    await landAfterSwitch(() => start(store), body);

    expect(store[flag]).toBe(false);
  });

  it('loadMoreAlbums releases the loading flag it also gates itself on', async () => {
    // Worse than the four above: `loadMoreAlbums` returns early while
    // `albumsLoading` is set, so a stranded flag also bars every later page.
    const firstPage = Array.from({ length: 40 }, (_, i) => ({ id: `nas-${i}` }));
    apiCall.get.mockResolvedValueOnce(ok({ albums: firstPage }));
    await store.loadAlbums();

    // Loaded once, so the switch refetches the grid for the new space too.
    await landAfterSwitch(
      () => store.loadMoreAlbums(),
      { albums: [{ id: 'nas-40' }] },
      { albums: [{ id: 'usb-1' }] }
    );

    expect(store.albums.map((a) => a.id)).toEqual(['usb-1']);
    expect(store.albumsLoading).toBe(false);
  });
});
