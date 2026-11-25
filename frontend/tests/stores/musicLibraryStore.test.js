// frontend/tests/stores/musicLibraryStore.test.js
/**
 * musicLibraryStore scopes every catalog read to ONE storage space (a Navidrome
 * library id), unless the user merged them. What can break that, silently, is
 * the selection: landing on a storage Navidrome cannot serve, a cached list
 * surviving a switch (the previous key's albums shown under the new one), a
 * dead library id outliving the storage it named — and the two opposite cases
 * an unplug and a deletion must NOT be confused with each other.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { nextTick } from 'vue';
import { useMusicLibraryStore } from '@/stores/musicLibraryStore';
import { useSettingsStore } from '@/stores/settingsStore';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { apiCall } from '@/services/apiCall';
import { resetApiCallMock, ok, fail } from '../helpers/apiCallMock';
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

  it('tags a queue addition with the storage space being browsed', async () => {
    apiCall.get.mockResolvedValueOnce(ok({ storages: [NAS, USB] }));
    await store.loadStorages();
    await nextTick();

    await store.addToQueue([{ id: 'tr-1' }], 'next');

    const [url, body] = apiCall.post.mock.calls.at(-1);
    expect(url).toBe('/api/audio/control/music_library');
    expect(body.command).toBe('add_to_queue');
    expect(body.data.library_id).toBe(NAS.library_id);
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

  it('keeps the window the user scrolled open when the index is revalidated', async () => {
    // A scan forces a reload of every cached list; collapsing the window each
    // time threw a list read 200 rows down back to 40.
    store.renderMoreArtists();
    expect(rendered()).toBe(60);

    apiCall.get.mockResolvedValueOnce(ok({ index: INDEX }));
    await store.loadArtists({ force: true });

    expect(rendered()).toBe(60);
  });

  it('starts another storage space at one chunk', async () => {
    apiCall.get.mockImplementation(async (url) =>
      url.endsWith('/storages') ? ok({ storages: [NAS, USB] }) : ok({ index: INDEX })
    );
    await store.loadStorages();
    await vi.waitFor(() => expect(store.artistsLoaded).toBe(true));
    store.renderMoreArtists();
    expect(rendered()).toBe(60);

    store.activeLibraryId = USB.library_id;
    await nextTick();
    await vi.waitFor(() => expect(store.artistsLoaded).toBe(true));

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
    kind: 'music_library', queue: TRACKS, queue_index: 1, queue_offset: 0, queue_length: 2, shuffle: false,
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

/**
 * A first read that failed had no state of its own: the skeleton stayed up for
 * good, which reads as a load still under way, and nothing reloaded it — every
 * reload refetched only the lists already loaded. The first opening of the
 * library starts Navidrome while the view mounts, so a 503 there is ordinary.
 */
describe('musicLibraryStore — a first read that failed', () => {
  let store;
  let unified;

  beforeEach(async () => {
    resetApiCallMock();
    store = useMusicLibraryStore();
    unified = useUnifiedAudioStore();
    apiCall.get.mockResolvedValueOnce(ok({ storages: [NAS], scanning: false }));
    await store.loadStorages();
    await nextTick();
  });

  it('says the catalog is starting for a 503, and reloads once it answers', async () => {
    apiCall.get.mockResolvedValueOnce(fail('Music library catalog not ready', 503));
    await store.loadAlbums();
    expect(store.albumsError).toBe('starting');
    expect(store.albumsLoaded).toBe(false);

    // The backend marks the catalog down on that 503, then up when it answers.
    publishState(unified, { availability: { music_library: 'catalog_unavailable' } });
    await nextTick();
    apiCall.get.mockResolvedValueOnce(ok({ albums: [{ id: 'a-1' }] }));
    publishState(unified, { availability: { music_library: null } });
    await nextTick();

    await vi.waitFor(() => expect(store.albums.map((a) => a.id)).toEqual(['a-1']));
    expect(store.albumsError).toBeNull();
  });

  it('says why the liked songs failed, as the other lists do, until a read lands', async () => {
    // Unread, the Liked Songs page said "No tracks" and its row "0 tracks"
    // while the playlists beside it said the catalog was starting.
    apiCall.get.mockResolvedValueOnce(fail('Music library catalog not ready', 503));
    await store.loadLikedSongs({ force: true });
    expect(store.likedSongsError).toBe('starting');
    expect(store.likedSongsLoaded).toBe(false);

    apiCall.get.mockResolvedValueOnce(fail('boom', 500));
    await store.loadLikedSongs({ force: true });
    expect(store.likedSongsError).toBe('failed');

    apiCall.get.mockResolvedValueOnce(ok({ songs: [{ id: 's1' }] }));
    await store.loadLikedSongs({ force: true });
    expect(store.likedSongsError).toBeNull();
    expect(store.likedSongsLoaded).toBe(true);
  });

  it('tells any other failure from a catalog that is starting', async () => {
    apiCall.get.mockResolvedValueOnce(fail('boom', 500));
    await store.loadGenres();

    expect(store.genresError).toBe('failed');
  });

  it('is reloaded by a resync, like a list that loaded', async () => {
    apiCall.get.mockResolvedValueOnce(fail('Music library catalog not ready', 503));
    await store.loadPlaylists();
    apiCall.get.mockClear();
    apiCall.get.mockImplementation(async (url) =>
      url.endsWith('/storages') ? ok({ storages: [NAS], scanning: false }) : ok({ playlists: [{ id: 'p1' }] })
    );

    await store.resync();

    expect(store.playlists.map((p) => p.id)).toEqual(['p1']);
    expect(store.playlistsError).toBeNull();
  });

  it('is only the latest read\'s to report, while it is on its way', async () => {
    // A failure overtaken by a newer read is no longer why nothing is shown:
    // the newer one may yet draw the list.
    const older = deferred();
    const newer = deferred();
    apiCall.get.mockReturnValueOnce(older.promise).mockReturnValueOnce(newer.promise);
    const stale = store.loadGenres({ force: true });
    const fresh = store.loadGenres({ force: true });

    older.settle(fail('boom', 500));
    await stale;
    expect(store.genresError).toBeNull();

    newer.settle(ok({ genres: [{ value: 'Jazz' }] }));
    await fresh;
    expect(store.genresError).toBeNull();
  });

  it('is only the latest read\'s to report, once it has failed too', async () => {
    // The newer read said "starting"; an older 500 landing after it must not
    // turn that into "failed" — a Retry button over a catalog that is booting.
    const older = deferred();
    const newer = deferred();
    apiCall.get.mockReturnValueOnce(older.promise).mockReturnValueOnce(newer.promise);
    const stale = store.loadGenres({ force: true });
    const fresh = store.loadGenres({ force: true });

    newer.settle(fail('Music library catalog not ready', 503));
    await fresh;
    older.settle(fail('boom', 500));
    await stale;

    expect(store.genresError).toBe('starting');
  });

  it('belongs to the scope it was read in: a switch asks again, from a clean slate', async () => {
    // A failed list was never `loaded`, so a reload keyed on that flag skipped
    // it, and the last scope's "starting" stayed over the new scope's grid.
    apiCall.get.mockResolvedValueOnce(ok({ storages: [NAS, USB], scanning: false }));
    await store.loadStorages({ force: true });
    await nextTick();
    expect(store.activeLibraryId).toBe(NAS.library_id);
    apiCall.get.mockResolvedValueOnce(fail('Music library catalog not ready', 503));
    await store.loadAlbums();
    expect(store.albumsError).toBe('starting');

    const onTheKey = deferred();
    apiCall.get.mockReturnValueOnce(onTheKey.promise);
    store.activeLibraryId = USB.library_id;
    await nextTick();
    await Promise.resolve();

    expect(paramsOf('/albums').library_id).toBe(USB.library_id);
    expect(store.albumsLoading).toBe(true);
    expect(store.albumsError).toBeNull();

    onTheKey.settle(ok({ albums: [{ id: 'usb-1' }] }));
    await vi.waitFor(() => expect(store.albums.map((a) => a.id)).toEqual(['usb-1']));
  });
});

describe('musicLibraryStore — reloads around a scan', () => {
  let store;

  const push = (overrides, scanning) =>
    store.handleStoragesEvent({ data: { storages: [{ ...NAS, ...overrides }], scanning } });
  const reads = (path) => apiCall.get.mock.calls.filter(([url]) => url.endsWith(path)).length;

  beforeEach(async () => {
    resetApiCallMock();
    store = useMusicLibraryStore();
    apiCall.get.mockResolvedValueOnce(ok({ storages: [{ ...NAS, album_count: 0 }], scanning: false }));
    await store.loadStorages();
    await nextTick();
    apiCall.get.mockImplementation(async (url) => {
      if (url.endsWith('/albums')) return ok({ albums: [] });
      if (url.endsWith('/starred')) return ok({ songs: [] });
      return ok({});
    });
    await store.loadAlbums();
    await store.loadLikedSongs();
    apiCall.get.mockClear();
  });

  afterEach(() => vi.restoreAllMocks());

  it('refetches the liked songs with the other lists when a scan ends', async () => {
    // A scan that marks tracks gone changes them as much as the albums, and
    // the Liked Songs row counts them.
    push({}, true);
    await nextTick();
    push({}, false);
    await nextTick();

    expect(reads('/starred')).toBe(1);
    expect(reads('/albums')).toBe(1);
  });

  it('paces the reloads a growing album count forces while the scan runs', async () => {
    // One storages push every 3 s for the whole scan: reloading every cached
    // list at each was some 1,400 requests per client over an 18-minute index.
    let now = 1_000_000;
    vi.spyOn(Date, 'now').mockImplementation(() => now);
    push({ album_count: 0 }, true);
    await nextTick();

    for (let count = 1; count <= 5; count += 1) {
      now += 3000;
      push({ album_count: count }, true);
      await nextTick();
    }
    expect(reads('/albums')).toBe(1);

    now += 30000;
    push({ album_count: 6 }, true);
    await nextTick();
    expect(reads('/albums')).toBe(2);
  });
});

/**
 * A drill-down page (an album, an artist, a playlist, a genre) folded every
 * failure into "nothing": a 503 while Navidrome restarts — with every backend
 * restart — or a dropped link read "Not found" over an album that exists, with
 * no way to ask again. The page needs to know which it is.
 */
describe('musicLibraryStore — a drill-down read that failed', () => {
  let store;

  beforeEach(() => {
    resetApiCallMock();
    store = useMusicLibraryStore();
  });

  it.each([
    ['fetchAlbum', 'al-1'],
    ['fetchArtist', 'ar-1'],
    ['fetchPlaylist', 'pl-1'],
  ])('%s tells a catalog that did not answer from an item it does not hold', async (fetch, id) => {
    apiCall.get.mockResolvedValueOnce(fail('Music library catalog not ready', 503));
    expect((await store[fetch](id)).error).toBe('unavailable');

    apiCall.get.mockResolvedValueOnce(fail('Network Error', null));
    expect((await store[fetch](id)).error).toBe('unavailable');

    apiCall.get.mockResolvedValueOnce(fail('Album not found', 404));
    expect(await store[fetch](id)).toEqual({ value: null, error: 'absent' });
  });

  it('plays an artist\'s albums whole or not at all', async () => {
    // One album that did not answer, and the others played as the artist.
    const album = (id, songs) => ok({ album: { id, song: songs.map((s) => ({ id: s })) } });
    apiCall.get
      .mockResolvedValueOnce(album('al-1', ['s-1', 's-2']))
      .mockResolvedValueOnce(fail('Music library catalog not ready', 503));
    expect(await store.fetchAlbumsSongs(['al-1', 'al-2']))
      .toEqual({ value: null, error: 'unavailable', failed: ['al-2'] });

    apiCall.get
      .mockResolvedValueOnce(album('al-1', ['s-1', 's-2']))
      .mockResolvedValueOnce(album('al-2', ['s-3']));
    const { value, error } = await store.fetchAlbumsSongs(['al-1', 'al-2']);
    expect(error).toBeNull();
    expect(value.map((s) => s.id)).toEqual(['s-1', 's-2', 's-3']);
  });

  it('names every album of an artist that did not load, in the order asked', async () => {
    // The page names them in its banner: playing nothing without a word was
    // the albums lost in silence.
    const album = (id) => ok({ album: { id, song: [{ id: `${id}-s` }] } });
    apiCall.get
      .mockResolvedValueOnce(fail('Music library catalog not ready', 503))
      .mockResolvedValueOnce(album('al-2'))
      .mockResolvedValueOnce(fail('Album not found', 404));

    const { value, failed } = await store.fetchAlbumsSongs(['al-1', 'al-2', 'al-3']);

    expect(value).toBeNull();
    expect(failed).toEqual(['al-1', 'al-3']);
  });

  it('says a genre whose tabs did not load failed, not that it is empty', async () => {
    apiCall.get.mockResolvedValue(fail('Music library catalog not ready', 503));

    expect((await store.fetchGenreSongs('Jazz')).error).toBe('unavailable');
    expect((await store.fetchGenreAlbums('Jazz')).error).toBe('unavailable');
  });
});

/**
 * A genre's songs came in one page of 500, the route's ceiling, and the page
 * counted, played and shuffled that page as the whole genre: a genre of 3,412
 * tracks opened on "500 tracks", and the rest never played.
 */
describe('musicLibraryStore — every song of a genre', () => {
  let store;
  const page = (from, n) => ok({ songs: Array.from({ length: n }, (_, i) => ({ id: `s-${from + i}` })) });
  const genreReads = () => apiCall.get.mock.calls.filter(([url]) => url.endsWith('/genre-songs'));

  beforeEach(async () => {
    resetApiCallMock();
    store = useMusicLibraryStore();
    apiCall.get.mockResolvedValueOnce(ok({ storages: [NAS, USB] }));
    await store.loadStorages();
    await nextTick();
  });

  it('reads page after page until a short one', async () => {
    apiCall.get
      .mockResolvedValueOnce(page(0, 500))
      .mockResolvedValueOnce(page(500, 500))
      .mockResolvedValueOnce(page(1000, 12));

    const { value, error } = await store.fetchGenreSongs('Jazz');

    expect(error).toBeNull();
    expect(value).toHaveLength(1012);
    expect(value.at(-1).id).toBe('s-1011');
    expect(genreReads().map(([, { params }]) => params.offset)).toEqual([0, 500, 1000]);
  });

  it('asks every page of the storage space the walk began in', async () => {
    // A switch while the walk is under way must not mix two spaces' songs.
    const first = deferred();
    apiCall.get.mockReturnValueOnce(first.promise).mockResolvedValueOnce(page(500, 3));
    const walk = store.fetchGenreSongs('Jazz');
    store.activeLibraryId = USB.library_id;
    first.settle(page(0, 500));
    await walk;

    expect(genreReads().map(([, { params }]) => params.library_id))
      .toEqual([NAS.library_id, NAS.library_id]);
  });

  it('fails the whole genre when a later page fails, rather than cut it short', async () => {
    apiCall.get
      .mockResolvedValueOnce(page(0, 500))
      .mockResolvedValueOnce(fail('Music library catalog not ready', 503));

    expect(await store.fetchGenreSongs('Jazz')).toEqual({ value: null, error: 'unavailable' });
  });

  it('stops on a full page it has already read, rather than walk forever', async () => {
    // An offset lost between the store and Navidrome answers the first page
    // again at every offset: the walk would never reach a short page.
    apiCall.get.mockResolvedValue(page(0, 500));

    expect(await store.fetchGenreSongs('Jazz')).toEqual({ value: null, error: 'unavailable' });
    expect(genreReads()).toHaveLength(2);
  });

  it('stops walking once told its page has moved on or closed', async () => {
    // A large genre left at once went on reading every page behind it.
    let current = true;
    apiCall.get.mockImplementation(async () => {
      current = false;
      return page(0, 500);
    });

    await store.fetchGenreSongs('Jazz', () => current);

    expect(genreReads()).toHaveLength(1);
  });
});

/**
 * A search that failed changed nothing: the results of the term before stayed
 * on screen under the new one, without a word — Bach's albums listed under
 * "beatles" while Navidrome restarted.
 */
describe('musicLibraryStore — a search that failed', () => {
  let store;

  beforeEach(() => {
    resetApiCallMock();
    store = useMusicLibraryStore();
  });

  it('drops the previous term\'s results and says the search failed', async () => {
    apiCall.get.mockResolvedValueOnce(ok({ artists: [], albums: [], songs: [{ id: 's-bach' }] }));
    store.searchTerm = 'bach';
    await store.search();

    apiCall.get.mockResolvedValueOnce(fail('Music library catalog not ready', 503));
    store.searchTerm = 'beatles';
    await store.search();

    expect(store.searchFailed).toBe(true);
    expect(store.lastSearchTerm).toBe('beatles');
    expect(store.searchResults.songs).toEqual([]);
    expect(store.hasSearched).toBe(false);
  });

  it('clears the failure once a search goes through, and with the field', async () => {
    apiCall.get.mockResolvedValueOnce(fail('Music library catalog not ready', 503));
    store.searchTerm = 'beatles';
    await store.search();

    apiCall.get.mockResolvedValueOnce(ok({ artists: [], albums: [], songs: [{ id: 's-1' }] }));
    await store.search();
    expect(store.searchFailed).toBe(false);
    expect(store.searchResults.songs.map((s) => s.id)).toEqual(['s-1']);

    apiCall.get.mockResolvedValueOnce(fail('Music library catalog not ready', 503));
    await store.search();
    store.clearSearch();
    expect(store.searchFailed).toBe(false);
  });
});

/**
 * The "add to playlist" picker and the playlist page share one playlist. A
 * membership read that failed was taken for "in no playlist": every row drew
 * unchecked, and a tap on one already holding the songs added them twice. A
 * picker write the page did not hear of left it on a copy the next edit was
 * refused from (409, a banner, the rows undone).
 */
describe('musicLibraryStore — the picker and the playlist on screen', () => {
  let store;

  beforeEach(() => {
    resetApiCallMock();
    store = useMusicLibraryStore();
  });

  it('tells a membership read that failed from songs in no playlist', async () => {
    apiCall.get.mockResolvedValueOnce(fail('Music library catalog not ready', 503));
    expect(await store.fetchPlaylistsContaining(['s-1'])).toBeNull();

    apiCall.get.mockResolvedValueOnce(ok({ playlist_ids: [] }));
    expect(await store.fetchPlaylistsContaining(['s-1'])).toEqual(new Set());
  });

  it('tells the playlist page of every write the picker makes to its entries', async () => {
    apiCall.put.mockResolvedValue(ok({ status: 'success' }));

    await store.addToPlaylist('pl-1', ['s-1']);
    const afterAdd = store.playlistWrites['pl-1'];
    await store.removeFromPlaylist('pl-1', ['s-1']);

    expect(afterAdd).toBeDefined();
    expect(store.playlistWrites['pl-1']).not.toBe(afterAdd);
    expect(store.playlistWrites['pl-2']).toBeUndefined();
  });

  it('leaves the page alone for a write that failed, and for its own edits', async () => {
    apiCall.put.mockResolvedValueOnce(fail('Navidrome rejected playlist update', 502));
    await store.addToPlaylist('pl-1', ['s-1']);

    apiCall.put.mockResolvedValue(ok({ status: 'success' }));
    await store.setPlaylistTracks('pl-1', ['s-1', 's-2'], ['s-2', 's-1']);
    await store.renamePlaylist('pl-1', 'Road');

    expect(store.playlistWrites['pl-1']).toBeUndefined();
  });
});

/**
 * The queue view lists the whole queue, read from GET /queue, under the
 * window the state carries. Its index is only as good as the read: a read
 * that lands after a newer one, or after the view closed, would list a queue
 * the backend has moved past; an edit the read does not hold yet must stay
 * laid over it until one does, and a refused one must not.
 */
describe('musicLibraryStore — the whole queue under the window', () => {
  let store;
  let unified;
  const WHOLE = Array.from({ length: 100 }, (_, n) => ({ id: `h${n}` }));
  const queueReads = () => apiCall.get.mock.calls.filter(([url]) => url === '/api/music-library/queue');
  const flushPromises = () => new Promise((resolve) => { setTimeout(resolve, 0); });

  beforeEach(() => {
    resetApiCallMock();
    store = useMusicLibraryStore();
    unified = useUnifiedAudioStore();
    publishState(unified, {
      source: 'music_library', service: 'running', session: makeSession(),
      details: {
        kind: 'music_library', queue: WHOLE.slice(0, 33), queue_index: 0, queue_offset: 0,
        queue_length: 100, shuffle: false, repeat: 'off', track_id: 'h0', album_id: null,
        artist_id: null, context_id: null,
      },
      controls: ['pause', 'play_index', 'remove_from_queue', 'move_in_queue'],
    });
  });

  it('keeps the newer of two reads whichever lands last', async () => {
    const older = deferred();
    const newer = deferred();
    apiCall.get.mockReturnValueOnce(older.promise).mockReturnValueOnce(newer.promise);

    const stale = store.loadWholeQueue();
    const fresh = store.loadWholeQueue();
    newer.settle(ok({ queue: WHOLE, queue_index: 0 }));
    await fresh;
    older.settle(ok({ queue: WHOLE.slice(0, 99), queue_index: 0 }));
    await stale;

    expect(store.wholeQueue).toBe(WHOLE);
  });

  it('lays an edit over the whole queue until a read made after its answer lands', async () => {
    apiCall.get.mockResolvedValueOnce(ok({ queue: WHOLE, queue_index: 0 }));
    await store.loadWholeQueue();
    const answer = deferred();
    apiCall.post.mockReturnValueOnce(answer.promise);

    const removed = store.removeFromQueue(80, 'h80');
    expect(store.wholeQueueEdits).toEqual([{ index: 80 }]);

    // A read issued before the backend answered may not hold the edit yet.
    apiCall.get.mockResolvedValueOnce(ok({ queue: WHOLE, queue_index: 0 }));
    await store.loadWholeQueue();
    expect(store.wholeQueueEdits).toHaveLength(1);

    const after = WHOLE.filter((_, n) => n !== 80);
    apiCall.get.mockResolvedValueOnce(ok({ queue: after, queue_index: 0 }));
    const reads = queueReads().length;
    answer.settle(ok({ status: 'success' }));
    expect(await removed).toBe(true);
    await flushPromises();

    expect(queueReads()).toHaveLength(reads + 1);
    expect(store.wholeQueue).toBe(after);
    expect(store.wholeQueueEdits).toEqual([]);
  });

  it('keeps an edit made before the view\'s first read landed', async () => {
    store.holdWholeQueue();
    const first = deferred();
    apiCall.get.mockReturnValueOnce(first.promise);
    const read = store.loadWholeQueue();
    const answer = deferred();
    apiCall.post.mockReturnValueOnce(answer.promise);

    const moved = store.moveInQueue(3, 8, 'h3');
    expect(store.wholeQueueEdits).toEqual([{ from: 3, to: 8 }]);

    // The first read was issued before the answer: it may not hold the move.
    first.settle(ok({ queue: WHOLE, queue_index: 0 }));
    await read;
    expect(store.wholeQueueEdits).toHaveLength(1);

    apiCall.get.mockResolvedValueOnce(ok({ queue: WHOLE, queue_index: 0 }));
    answer.settle(ok({ status: 'success' }));
    expect(await moved).toBe(true);
    await flushPromises();
    expect(store.wholeQueueEdits).toEqual([]);
  });

  it('drops a refused edit at once and reads the queue again', async () => {
    apiCall.get.mockResolvedValue(ok({ queue: WHOLE, queue_index: 0 }));
    await store.loadWholeQueue();
    apiCall.post.mockResolvedValueOnce(fail('Refused', 409));
    const reads = queueReads().length;

    expect(await store.moveInQueue(90, 20, 'h90')).toBe(false);

    expect(store.wholeQueueEdits).toEqual([]);
    expect(queueReads()).toHaveLength(reads + 1);
  });

  it('keeps nothing once the view released it, not even a read still on its way', async () => {
    const late = deferred();
    apiCall.get.mockReturnValueOnce(late.promise);
    const release = store.holdWholeQueue();

    const read = store.loadWholeQueue();
    release();
    late.settle(ok({ queue: WHOLE, queue_index: 0 }));
    await read;
    await store.removeFromQueue(80, 'h80');

    expect(store.wholeQueue).toBeNull();
    expect(store.wholeQueueEdits).toEqual([]);
  });

  // The navigation keeps a popped queue view mounted until its pages settle:
  // opened again meanwhile, the new view must not lose its queue — nor the
  // read it has on its way — when the old one lets go, or it lists the
  // window alone until the queue next moves.
  it('keeps the queue while another view still holds it', async () => {
    apiCall.get.mockResolvedValueOnce(ok({ queue: WHOLE, queue_index: 0 }));
    const releaseOld = store.holdWholeQueue();
    await store.loadWholeQueue();
    const inFlight = deferred();
    apiCall.get.mockReturnValueOnce(inFlight.promise);
    const releaseNew = store.holdWholeQueue();
    const read = store.loadWholeQueue();

    releaseOld();
    releaseOld();
    const after = WHOLE.slice(0, 99);
    inFlight.settle(ok({ queue: after, queue_index: 0 }));
    await read;
    expect(store.wholeQueue).toBe(after);

    releaseNew();
    expect(store.wholeQueue).toBeNull();
  });
});
