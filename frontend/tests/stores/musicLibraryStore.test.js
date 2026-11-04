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
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { apiCall } from '@/services/apiCall';
import { resetApiCallMock, ok } from '../helpers/apiCallMock';
import { publishState, makeSession } from '../helpers/audioState';

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

  /**
   * The A-Z rail jumps to a letter whose rows the window has not mounted, so
   * these two are what make it land anywhere at all: the rail must offer the
   * WHOLE index, and a jump must widen the window through its target. Broken,
   * the rail silently does nothing for every letter past the first chunk.
   */
  it('offers the whole index on the rail, not the mounted part of it', () => {
    expect(store.artistRailLetters).toEqual(['A', 'B', 'C']);
    expect(store.displayedArtistIndex.map((b) => b.name)).toEqual(['A', 'B']);
  });

  // Five 30-artist buckets: 'E' sits three chunks past the window, so a jump that
  // widens by one chunk lands short of it — which a one-chunk index would hide.
  const DEEP_INDEX = ['A', 'B', 'C', 'D', 'E'].map((name) => ({
    name,
    artist: Array.from({ length: 30 }, (_, i) => ({ id: `${name}${i}`, name: `${name}${i}` })),
  }));

  async function loadDeepIndex() {
    apiCall.get.mockResolvedValueOnce(ok({ index: DEEP_INDEX }));
    await store.loadArtists({ force: true });
  }

  it('mounts through the bucket a jump targets, however far down it sits', async () => {
    await loadDeepIndex();

    store.renderArtistsThrough('E');

    expect(rendered()).toBe(150);
    expect(store.displayedArtistIndex.at(-1).name).toBe('E');
  });

  it('never shrinks the window when the drag comes back up the rail', async () => {
    await loadDeepIndex();

    store.renderArtistsThrough('E');
    store.renderArtistsThrough('A');

    expect(rendered()).toBe(150);
  });

  it('leaves the window alone for a letter the index does not carry', () => {
    store.renderArtistsThrough('Z');

    expect(rendered()).toBe(40);
  });

  it('offers no rail while the list barely outgrows one screenful', async () => {
    apiCall.get.mockResolvedValueOnce(ok({
      index: [{ name: 'A', artist: Array.from({ length: 40 }, (_, i) => ({ id: `a${i}`, name: `A${i}` })) }],
    }));
    await store.loadArtists({ force: true });

    expect(store.artistRailLetters).toEqual([]);
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

  it('loadLikedSongs keeps its flag up while a newer load is still in flight', async () => {
    // LikedSongsView reads the flag to tell "loading" from "no tracks": an
    // older load finishing first used to lower it over the one still coming.
    let releaseOld;
    let releaseNew;
    apiCall.get
      .mockReturnValueOnce(new Promise((resolve) => { releaseOld = () => resolve(ok({ songs: [] })); }))
      .mockReturnValueOnce(new Promise((resolve) => { releaseNew = () => resolve(ok({ songs: [{ id: 's1' }] })); }));
    const older = store.loadLikedSongs({ force: true });
    const newer = store.loadLikedSongs({ force: true });

    releaseOld();
    await older;
    expect(store.likedSongsLoading).toBe(true);

    releaseNew();
    await newer;
    expect(store.likedSongsLoading).toBe(false);
  });

  it('loadLikedSongs keeps the newer list when the older load lands last', async () => {
    // A list read before a storage change must not overwrite the one read after.
    let releaseOld;
    let releaseNew;
    apiCall.get
      .mockReturnValueOnce(new Promise((resolve) => { releaseOld = () => resolve(ok({ songs: [{ id: 'old' }] })); }))
      .mockReturnValueOnce(new Promise((resolve) => { releaseNew = () => resolve(ok({ songs: [{ id: 'new' }] })); }));
    const older = store.loadLikedSongs({ force: true });
    const newer = store.loadLikedSongs({ force: true });

    releaseNew();
    await newer;
    releaseOld();
    await older;

    expect(store.likedSongs.map((s) => s.id)).toEqual(['new']);
  });
});

/**
 * A revalidation is not a first load. LibraryHome asks for a scan every time it
 * mounts, and the store refetches every cached list when that scan ends — so
 * emptying the grid first swapped it for the skeleton and remounted every cover
 * on a plain page load, three seconds in, for a scan that had found nothing.
 * The three sibling loaders (artists, genres, playlists) already replace their
 * list on arrival; these hold the albums grid to the same rule.
 */
describe('musicLibraryStore — revalidating the albums grid', () => {
  let store;
  const page = (prefix, n) => Array.from({ length: n }, (_, i) => ({ id: `${prefix}-${i}` }));

  beforeEach(async () => {
    resetApiCallMock();
    store = useMusicLibraryStore();
    apiCall.get.mockResolvedValueOnce(ok({ storages: [NAS] }));
    await store.loadStorages();
    await nextTick();
  });

  it('keeps the covers on screen while the refetch is in flight', async () => {
    apiCall.get.mockResolvedValueOnce(ok({ albums: page('a', 40) }));
    await store.loadAlbums();

    let release;
    apiCall.get.mockReturnValueOnce(
      new Promise((resolve) => { release = () => resolve(ok({ albums: page('a', 40) })); })
    );
    const inFlight = store.loadAlbums({ force: true });
    await nextTick();

    // The grid must never fall back to the empty state a skeleton is drawn for.
    expect(store.albums).toHaveLength(40);
    release();
    await inFlight;
    expect(store.albums).toHaveLength(40);
  });

  it('re-reads every page the user has already scrolled open', async () => {
    apiCall.get.mockResolvedValueOnce(ok({ albums: page('a', 40) }));
    await store.loadAlbums();
    apiCall.get.mockResolvedValueOnce(ok({ albums: page('b', 40) }));
    await store.loadMoreAlbums();
    expect(store.albums).toHaveLength(80);

    apiCall.get.mockResolvedValueOnce(ok({ albums: page('c', 80) }));
    await store.loadAlbums({ force: true });

    // Asking for one page would drop 40 albums out from under the scroll.
    expect(paramsOf('/albums').size).toBe(80);
    expect(paramsOf('/albums').offset).toBe(0);
    expect(store.albums).toHaveLength(80);
    // A full answer at that size is not evidence of a further page.
    expect(store.albumsHasMore).toBe(true);
  });
});

/** A response the test lands by hand, to drive two requests out of order. */
const deferred = () => {
  let settle;
  const promise = new Promise((resolve) => { settle = resolve; });
  return { promise, settle };
};

/**
 * On the kiosk's slow keyboard every character past the debounce issues its
 * own search3, whose time depends on the term (6 ms to 1.2 s measured): the
 * response for a term already replaced, or already cleared, must not land over
 * the one the field now shows — a tap would then play something else than what
 * was searched.
 */
describe('musicLibraryStore — search supersession', () => {
  let store;
  const results = (term) => ok({ artists: [], albums: [], songs: [{ id: `s-${term}` }] });

  beforeEach(() => {
    resetApiCallMock();
    store = useMusicLibraryStore();
  });

  it('aborts the older search and keeps the spinner up for the newer one', async () => {
    const older = deferred();
    const newer = deferred();
    apiCall.get.mockReturnValueOnce(older.promise).mockReturnValueOnce(newer.promise);

    store.searchTerm = 'a';
    const stale = store.search();
    store.searchTerm = 'ab';
    const fresh = store.search();

    expect(apiCall.get.mock.calls[0][1].signal.aborted).toBe(true);
    expect(apiCall.get.mock.calls[1][1].signal.aborted).toBe(false);

    older.settle(results('a'));
    await stale;
    // The spinner belongs to the search still in flight.
    expect(store.searchLoading).toBe(true);

    newer.settle(results('ab'));
    await fresh;
    expect(store.lastSearchTerm).toBe('ab');
    expect(store.searchResults.songs.map((s) => s.id)).toEqual(['s-ab']);
    expect(store.searchLoading).toBe(false);
  });

  it('keeps the newer results when the older search lands last', async () => {
    const older = deferred();
    const newer = deferred();
    apiCall.get.mockReturnValueOnce(older.promise).mockReturnValueOnce(newer.promise);

    store.searchTerm = 'a';
    const stale = store.search();
    store.searchTerm = 'ab';
    const fresh = store.search();

    newer.settle(results('ab'));
    await fresh;
    older.settle(results('a'));
    await stale;

    expect(store.lastSearchTerm).toBe('ab');
    expect(store.searchResults.songs.map((s) => s.id)).toEqual(['s-ab']);
  });

  it('drops a search response that lands after clearSearch', async () => {
    const inFlight = deferred();
    apiCall.get.mockReturnValueOnce(inFlight.promise);

    store.searchTerm = 'a';
    const stale = store.search();
    store.clearSearch();
    expect(apiCall.get.mock.calls[0][1].signal.aborted).toBe(true);

    inFlight.settle(results('a'));
    await stale;

    expect(store.hasSearched).toBe(false);
    expect(store.searchResults).toEqual({ artists: [], albums: [], songs: [] });
    expect(store.searchLoading).toBe(false);
  });
});

/**
 * Two forced reloads of one list overlap when a scan ends while the view forces
 * its own: the list and its loading flag belong to the latest, or the grid from
 * before the scan stays on screen until the next one.
 */
describe('musicLibraryStore — a reload superseded by a newer one', () => {
  let store;

  beforeEach(async () => {
    resetApiCallMock();
    store = useMusicLibraryStore();
    apiCall.get.mockResolvedValueOnce(ok({ storages: [NAS] }));
    await store.loadStorages();
    await nextTick();
  });

  const LISTS = [
    ['loadAlbums', 'albumsLoading', (ids) => ({ albums: ids.map((id) => ({ id })) }), (s) => s.albums.map((a) => a.id)],
    ['loadArtists', 'artistsLoading', (ids) => ({ index: ids.map((name) => ({ name, artist: [{ id: name }] })) }), (s) => s.displayedArtistIndex.map((b) => b.name)],
    ['loadGenres', 'genresLoading', (ids) => ({ genres: ids.map((value) => ({ value })) }), (s) => s.genres.map((g) => g.value)],
    ['loadPlaylists', 'playlistsLoading', (ids) => ({ playlists: ids.map((id) => ({ id })) }), (s) => s.playlists.map((p) => p.id)],
  ];

  it.each(LISTS)('%s: a forced reload that lands last does not overwrite the newer list', async (loader, flag, body, read) => {
    const older = deferred();
    const newer = deferred();
    apiCall.get.mockReturnValueOnce(older.promise).mockReturnValueOnce(newer.promise);

    const stale = store[loader]({ force: true });
    const fresh = store[loader]({ force: true });

    newer.settle(ok(body(['a0', 'a1', 'a2'])));
    await fresh;
    older.settle(ok(body(['a0', 'a1'])));
    await stale;

    expect(read(store)).toEqual(['a0', 'a1', 'a2']);
    expect(store[flag]).toBe(false);
  });

  it.each(LISTS)('%s draws an older reload that lands first, and keeps its flag up for the newer', async (loader, flag, body, read) => {
    // During a scan a reload is forced at every storages push, and one can
    // outlast the next: dropping every reload a newer one overtook would leave
    // the grid frozen for as long as the scan keeps them coming.
    const older = deferred();
    const newer = deferred();
    apiCall.get.mockReturnValueOnce(older.promise).mockReturnValueOnce(newer.promise);

    const stale = store[loader]({ force: true });
    const fresh = store[loader]({ force: true });

    older.settle(ok(body(['a0', 'a1'])));
    await stale;
    expect(read(store)).toEqual(['a0', 'a1']);
    expect(store[flag]).toBe(true);

    newer.settle(ok(body(['a0', 'a1', 'a2'])));
    await fresh;
    expect(read(store)).toEqual(['a0', 'a1', 'a2']);
    expect(store[flag]).toBe(false);
  });

  it('a next page does not append to the grid a forced reload replaced', async () => {
    const first = Array.from({ length: 40 }, (_, i) => ({ id: `old-${i}` }));
    apiCall.get.mockResolvedValueOnce(ok({ albums: first }));
    await store.loadAlbums();

    const nextPage = deferred();
    const reload = deferred();
    apiCall.get.mockReturnValueOnce(nextPage.promise).mockReturnValueOnce(reload.promise);
    const more = store.loadMoreAlbums();
    const forced = store.loadAlbums({ force: true });

    // Landing first, the page extends the grid still on screen, and must not
    // lower the flag of the reload still coming.
    nextPage.settle(ok({ albums: [{ id: 'old-40' }] }));
    await more;
    expect(store.albumsLoading).toBe(true);

    reload.settle(ok({ albums: [{ id: 'new-0' }] }));
    await forced;
    expect(store.albums.map((a) => a.id)).toEqual(['new-0']);
    expect(store.albumsLoading).toBe(false);
  });

  it('a next page that lands after a forced reload is dropped', async () => {
    const first = Array.from({ length: 40 }, (_, i) => ({ id: `old-${i}` }));
    apiCall.get.mockResolvedValueOnce(ok({ albums: first }));
    await store.loadAlbums();

    const nextPage = deferred();
    const reload = deferred();
    apiCall.get.mockReturnValueOnce(nextPage.promise).mockReturnValueOnce(reload.promise);
    const more = store.loadMoreAlbums();
    const forced = store.loadAlbums({ force: true });

    reload.settle(ok({ albums: [{ id: 'new-0' }] }));
    await forced;
    nextPage.settle(ok({ albums: [{ id: 'old-40' }] }));
    await more;

    expect(store.albums.map((a) => a.id)).toEqual(['new-0']);
    expect(store.albumsLoading).toBe(false);
  });
});

describe('musicLibraryStore — a catalog that was not answering', () => {
  let store;
  let unified;

  beforeEach(async () => {
    resetApiCallMock();
    store = useMusicLibraryStore();
    unified = useUnifiedAudioStore();
    // Navidrome is down: the storage list is still served from the mount table,
    // but every count reads zero and every catalog call answers empty. The
    // backend says so in the audio state's availability.
    apiCall.get.mockResolvedValueOnce(
      ok({ storages: [{ ...NAS, track_count: 0, album_count: 0 }], scanning: false })
    );
    await store.loadStorages();
    publishState(unified, { availability: { music_library: 'catalog_unavailable' } });
    await nextTick();
  });

  it('separates "not answering" from "nothing here"', () => {
    expect(store.catalogReady).toBe(false);
    // The state the view must NOT reach: it tells a user whose NAS is mounted
    // and full to go connect a NAS.
    expect(store.unindexedStorage).toBeNull();
  });

  it('refetches the lists it cached empty once Navidrome answers again', async () => {
    apiCall.get.mockResolvedValueOnce(ok({ albums: [] }));
    await store.loadAlbums();
    expect(store.albums).toHaveLength(0);

    apiCall.get.mockResolvedValueOnce(ok({ albums: [{ id: 'a-1' }, { id: 'a-2' }] }));
    publishState(unified, { availability: { music_library: null } });
    await nextTick();
    await vi.waitFor(() => expect(store.albums).toHaveLength(2));
  });

  it('does not refetch on a state that merely repeats the good news', async () => {
    publishState(unified, { availability: { music_library: null } });
    await nextTick();
    apiCall.get.mockClear();

    publishState(unified, { availability: { music_library: null } });
    await nextTick();
    expect(apiCall.get).not.toHaveBeenCalled();
  });
});

describe('musicLibraryStore — now playing', () => {
  let store;
  let unified;

  const TRACKS = [
    { id: 'tr-1', title: 'Angel', albumId: 'al-42', artistId: 'ar-7' },
    { id: 'tr-2', title: 'Risingson', albumId: 'al-42', artistId: 'ar-7' },
  ];
  const details = (overrides = {}) => ({
    kind: 'music_library', queue: TRACKS, queue_index: 1, shuffle: false,
    track_id: 'tr-2', album_id: 'al-42', artist_id: 'ar-7', ...overrides,
  });

  beforeEach(() => {
    resetApiCallMock();
    store = useMusicLibraryStore();
    unified = useUnifiedAudioStore();
  });

  it('names the live track from the session and navigates by the details ids', () => {
    publishState(unified, {
      source: 'music_library', service: 'running',
      session: makeSession({ phase: 'paused', title: 'Risingson', artist: 'Massive Attack',
        album: 'Mezzanine', artwork: '/api/music-library/cover/al-42' }),
      details: details(),
      controls: ['resume', 'seek', 'prev', 'set_shuffle', 'play_index', 'stop'],
    });

    expect(store.nowPlaying).toMatchObject({
      trackId: 'tr-2', title: 'Risingson', albumId: 'al-42', artistId: 'ar-7',
      albumArtUrl: '/api/music-library/cover/al-42',
    });
    expect(store.queueIndex).toBe(1);
    expect(store.isPlaying).toBe(false);
  });

  it('keeps the saved queue a play press reopens after the session ended', () => {
    publishState(unified, {
      source: 'music_library', service: 'running',
      resume: { title: 'Risingson', artist: 'Massive Attack', album: 'Mezzanine',
        artwork: null, duration_ms: 300000, position_ms: 81000 },
      details: details(),
      controls: ['resume', 'play_index', 'stop'],
    });

    expect(store.nowPlaying?.title).toBe('Risingson');
  });

  it('has nothing to show once the queue is gone', () => {
    publishState(unified, { source: 'music_library', service: 'running' });
    expect(store.nowPlaying).toBeNull();
    expect(store.queue).toEqual([]);
  });
});
