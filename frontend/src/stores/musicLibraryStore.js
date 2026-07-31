// frontend/src/stores/musicLibraryStore.js
//
// Central state for the Music Library source (Family C). Two concerns:
//
//   1. Now-playing — DERIVED from the central audio mirror
//      (unifiedAudioStore.systemState.metadata) gated on active_source ===
//      'music_library', exactly like cdStore. The backend broadcasts the queue
//      projection (title/artist/album/art + queue/index/shuffle) as
//      standard source metadata, healed by full_state on every reconnect, so
//      there is no delta-fed now-playing state to maintain here.
//
//   2. Catalog — browse/search/playlist data fetched on demand from
//      /api/music-library/* (Navidrome via the backend proxy) and cached so
//      revisiting a tab is instant. Favorites (star) live in Navidrome and ride
//      the browse payloads' `starred` field; toggling optimistically overrides
//      it locally since the backend emits no favorite WS event for this source.
//
//      Every catalog read is scoped to ONE storage space (`activeLibraryId`, a
//      Navidrome library id — see backend libraries.py). There is no "all
//      storages" mode: with a single storage the scope is invisible, and with
//      two or more the user picks one. That includes playlists and liked songs —
//      Navidrome keeps both catalog-wide, and the backend narrows them, because
//      a playlist spanning a NAS and a USB key is what the filter exists to
//      prevent.
import { defineStore } from 'pinia';
import { ref, computed, watch } from 'vue';
import { apiCall } from '@/services/apiCall';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';

const BASE = '/api/music-library';
const ALBUMS_PAGE_SIZE = 40;
// Artists arrive in one call, so this paces the RENDER, not the fetch.
const ARTISTS_RENDER_CHUNK = 40;
// Cover sizes (square max dimension Navidrome resizes to), scaled by
// devicePixelRatio (capped at 2) so covers stay crisp on HiDPI screens. The
// player uses the backend-provided full-size album_art_url as-is.
//   - grid: AlbumCard fills 1fr columns that render up to ~350px CSS.
//   - row:  MediaRow / picker thumbnails render at ~60px CSS.
const _DPR = Math.min(window.devicePixelRatio || 1, 2);
const COVER_GRID_PX = Math.round(350 * _DPR);
const COVER_ROW_PX = Math.round(80 * _DPR);

export const useMusicLibraryStore = defineStore('musicLibrary', () => {
  const unifiedStore = useUnifiedAudioStore();

  // Proxy URL for a Navidrome cover id (album/song coverArt id). size omitted
  // → original bytes.
  function coverUrl(coverId, size = null) {
    if (!coverId) return '';
    return size ? `${BASE}/cover/${coverId}?size=${size}` : `${BASE}/cover/${coverId}`;
  }
  // Small rounded thumbnails in lists and the playlist picker (~60px CSS).
  function thumbUrl(coverId) {
    return coverUrl(coverId, COVER_ROW_PX);
  }
  // Full-width album covers in browse grids.
  function gridUrl(coverId) {
    return coverUrl(coverId, COVER_GRID_PX);
  }

  // =========================================================================
  // STORAGE SPACES — the USB keys and network shares music comes from. One
  // Navidrome library each, so `library_id` is what scopes a catalog read.
  // =========================================================================
  const storages = ref([]);
  const storagesLoaded = ref(false);
  // The storage space being browsed. Never null once a storage exists: there is
  // no "all storages" view (see the header note).
  const activeLibraryId = ref(null);

  // Only a storage Navidrome has accepted can be browsed; one still waiting for
  // its library is listed by the API but cannot be a filter option.
  const browsableStorages = computed(() =>
    storages.value.filter((s) => s.library_id != null)
  );

  async function loadStorages({ force = false } = {}) {
    if (storagesLoaded.value && !force) return;
    const result = await apiCall.get(`${BASE}/storages`, {
      category: 'musicLibrary',
      message: 'Error loading storage spaces',
      logLevel: 'debug',
    });
    if (result.ok && Array.isArray(result.data?.storages)) {
      storages.value = result.data.storages;
      storagesLoaded.value = true;
    }
  }

  // Keep the selection on a storage that still exists: a key unplugged while
  // its own view is open would otherwise browse a library that is gone.
  watch(browsableStorages, (list) => {
    if (!list.length) {
      activeLibraryId.value = null;
    } else if (!list.some((s) => s.library_id === activeLibraryId.value)) {
      activeLibraryId.value = list[0].library_id;
    }
  }, { immediate: true });

  // Catalog params for the active storage space.
  const scoped = (params = {}) =>
    activeLibraryId.value == null
      ? params
      : { ...params, library_id: activeLibraryId.value };

  // Every scoped fetch is issued against the storage space that was active when
  // it left, and the user can switch while it is in flight. Capturing the scope
  // and dropping a response that no longer matches is what keeps one storage's
  // albums out of another's grid — the lists below are shared, so a late
  // response would otherwise overwrite (or append to) the wrong catalog.
  const inScope = (scope) => scope === activeLibraryId.value;

  // =========================================================================
  // NOW PLAYING (derived from the central mirror)
  // =========================================================================
  const meta = computed(() =>
    unifiedStore.systemState.active_source === 'music_library'
      ? (unifiedStore.systemState.metadata || {})
      : {}
  );

  const queue = computed(() => meta.value.queue || []);
  const queueIndex = computed(() => meta.value.queue_index ?? -1);
  const shuffle = computed(() => !!meta.value.shuffle);
  const currentTrackId = computed(() => meta.value.track_id || null);
  const isPlaying = computed(() => !!meta.value.is_playing);

  // Live now-playing, or null when the queue is cleared (WAITING).
  const nowPlaying = computed(() => {
    const m = meta.value;
    if (!m.track_id && !m.title) return null;
    return {
      trackId: m.track_id,
      title: m.title,
      artist: m.artist,
      album: m.album,
      albumId: m.album_id,
      artistId: m.artist_id,
      albumArtUrl: m.album_art_url,
    };
  });

  // Sticky copy preserved through the player's fade-out: the backend clears the
  // queue metadata the instant it drops to WAITING, so binding the docked
  // player straight to nowPlaying would blank its artwork/title mid-fade. The
  // component clears this once the fade completes (mirrors podcast displayEpisode).
  const displayTrack = ref(null);
  watch(nowPlaying, (np) => { if (np) displayTrack.value = np; }, { immediate: true });
  function clearDisplayTrack() { displayTrack.value = null; }

  // =========================================================================
  // FAVORITES — starred songs behind the virtual "Liked Songs" playlist
  // =========================================================================
  const likedSongs = ref([]);
  const likedSongIds = ref(new Set());
  const likedSongsLoaded = ref(false);

  async function loadLikedSongs({ force = false } = {}) {
    if (likedSongsLoaded.value && !force) return;
    const scope = activeLibraryId.value;
    const result = await apiCall.get(`${BASE}/starred`, {
      category: 'musicLibrary',
      message: 'Error loading liked songs',
      checkStatus: true,
      params: scoped(),
    });
    if (!inScope(scope)) return;
    if (result.ok && Array.isArray(result.data?.songs)) {
      likedSongs.value = result.data.songs;
      likedSongIds.value = new Set(result.data.songs.map((s) => s.id));
      likedSongsLoaded.value = true;
    }
  }

  const isSongLiked = (id) => likedSongIds.value.has(id);
  const likedSongsCount = computed(() => likedSongIds.value.size);

  async function setSongFavorite(id, on) {
    if (!id || isSongLiked(id) === on) return true;
    const removed = on ? null : likedSongs.value.find((s) => s.id === id) || null;
    const nextIds = new Set(likedSongIds.value);
    if (on) nextIds.add(id);
    else nextIds.delete(id);
    likedSongIds.value = nextIds;
    if (!on) likedSongs.value = likedSongs.value.filter((s) => s.id !== id);

    const result = await apiCall.post(`${BASE}/${on ? 'star' : 'unstar'}`,
      { id, kind: 'song' }, {
        category: 'musicLibrary',
        message: `Error ${on ? 'starring' : 'unstarring'} song`,
      });

    if (!result.ok || result.data?.status !== 'success') {
      const revert = new Set(likedSongIds.value);
      if (on) revert.delete(id);
      else revert.add(id);
      likedSongIds.value = revert;
      if (removed) likedSongs.value = [...likedSongs.value, removed];
      return false;
    }
    return true;
  }

  // Star state of the currently-playing track (for the docked player heart).
  const currentStarred = computed(() => {
    const id = currentTrackId.value;
    if (!id) return false;
    if (likedSongsLoaded.value) return isSongLiked(id);
    return !!queue.value[queueIndex.value]?.starred;
  });
  function toggleCurrentStar() {
    return setSongFavorite(currentTrackId.value, !currentStarred.value);
  }

  // =========================================================================
  // TRANSPORT (thin wrappers over the generic control endpoint)
  // =========================================================================
  const send = (command, data = {}) =>
    unifiedStore.sendCommand('music_library', command, data);

  // Build a gapless queue from an ordered list of Subsonic song dicts, starting
  // at startIndex. shuffle is honoured at play time by the backend.
  function playContext(tracks, startIndex = 0, shuffleOn = false) {
    if (!tracks?.length) return Promise.resolve(false);
    return send('play_context', {
      tracks,
      start_index: startIndex,
      shuffle: shuffleOn,
    });
  }
  const playIndex = (index) => send('play_index', { index });
  const pause = () => send('pause');
  const resume = () => send('resume');
  const next = () => send('next');
  const previous = () => send('prev');
  // Swipe-prev always steps to the actual previous track. The button's previous()
  // ('prev') restarts the current track when >3s in — right for a tap, wrong under
  // the swipe carousel, which is already showing the previous track's text and
  // would glitch back to the current one on a restart. play_index skips outright.
  const swipePrevious = () =>
    queueIndex.value > 0 ? playIndex(queueIndex.value - 1) : Promise.resolve(false);
  const stop = () => send('stop');
  // Live shuffle toggle: reorders only the upcoming tracks (the current one keeps
  // playing). Sends the target state, not a flip, so a stale tap can't invert it.
  const setShuffle = (on) => send('set_shuffle', { shuffle: !!on });
  const toggleShuffle = () => setShuffle(!shuffle.value);

  // =========================================================================
  // CATALOG — Albums (home Albums tab; getAlbumList2 alphabetical, paged)
  // =========================================================================
  const albums = ref([]);
  const albumsLoading = ref(false);
  const albumsLoaded = ref(false);
  const albumsHasMore = ref(true);

  async function loadAlbums({ reset = false } = {}) {
    if (albumsLoaded.value && !reset) return;
    const scope = activeLibraryId.value;
    albumsLoading.value = true;
    if (reset) {
      albums.value = [];
      albumsHasMore.value = true;
    }
    const result = await apiCall.get(`${BASE}/albums`, {
      category: 'musicLibrary',
      message: 'Error loading albums',
      checkStatus: true,
      params: scoped({ type: 'alphabeticalByName', size: ALBUMS_PAGE_SIZE, offset: 0 }),
    });
    if (!inScope(scope)) return;
    if (result.ok && Array.isArray(result.data?.albums)) {
      albums.value = result.data.albums;
      albumsHasMore.value = result.data.albums.length >= ALBUMS_PAGE_SIZE;
      albumsLoaded.value = true;
    }
    albumsLoading.value = false;
  }

  async function loadMoreAlbums() {
    if (albumsLoading.value || !albumsHasMore.value) return;
    const scope = activeLibraryId.value;
    albumsLoading.value = true;
    const result = await apiCall.get(`${BASE}/albums`, {
      category: 'musicLibrary',
      message: 'Error loading more albums',
      checkStatus: true,
      params: scoped({
        type: 'alphabeticalByName',
        size: ALBUMS_PAGE_SIZE,
        offset: albums.value.length,
      }),
    });
    if (!inScope(scope)) return;
    if (result.ok && Array.isArray(result.data?.albums)) {
      albums.value = [...albums.value, ...result.data.albums];
      albumsHasMore.value = result.data.albums.length >= ALBUMS_PAGE_SIZE;
    }
    albumsLoading.value = false;
  }

  // =========================================================================
  // CATALOG — Artists (getArtists → A–Z index buckets, single call)
  // =========================================================================
  const artistIndex = ref([]);
  const artistsLoading = ref(false);
  const artistsLoaded = ref(false);
  // Render window over the A–Z index. getArtists returns the WHOLE index in one
  // call (Subsonic has no offset there), so the paging is local: mounting every
  // row in one tick costs ~1.8 ms each — a second of frozen UI at 550 artists,
  // several at NAS scale. Extended by the home view's scroll sentinel, exactly
  // like the albums grid.
  const artistsRendered = ref(ARTISTS_RENDER_CHUNK);

  async function loadArtists({ force = false } = {}) {
    if (artistsLoaded.value && !force) return;
    const scope = activeLibraryId.value;
    artistsLoading.value = true;
    const result = await apiCall.get(`${BASE}/artists`, {
      category: 'musicLibrary',
      message: 'Error loading artists',
      checkStatus: true,
      params: scoped(),
    });
    if (!inScope(scope)) return;
    if (result.ok && Array.isArray(result.data?.index)) {
      artistIndex.value = result.data.index;
      artistsRendered.value = ARTISTS_RENDER_CHUNK;
      artistsLoaded.value = true;
    }
    artistsLoading.value = false;
  }

  const artistCount = computed(() =>
    artistIndex.value.reduce((n, bucket) => n + (bucket.artist?.length || 0), 0)
  );

  // Buckets truncated to the window. The budget counts artists, not buckets, so
  // one crowded letter can't blow the window open on its own.
  const displayedArtistIndex = computed(() => {
    let budget = artistsRendered.value;
    const out = [];
    for (const bucket of artistIndex.value) {
      if (budget <= 0) break;
      const artist = (bucket.artist || []).slice(0, budget);
      budget -= artist.length;
      if (artist.length) out.push({ ...bucket, artist });
    }
    return out;
  });

  const artistsHasMore = computed(() => artistsRendered.value < artistCount.value);

  // Local-only: nothing is fetched, the next slice is simply mounted.
  function renderMoreArtists() {
    if (artistsHasMore.value) artistsRendered.value += ARTISTS_RENDER_CHUNK;
  }

  // =========================================================================
  // CATALOG — Genres (getGenres, single call)
  // =========================================================================
  const genres = ref([]);
  const genresLoading = ref(false);
  const genresLoaded = ref(false);

  async function loadGenres({ force = false } = {}) {
    if (genresLoaded.value && !force) return;
    const scope = activeLibraryId.value;
    genresLoading.value = true;
    const result = await apiCall.get(`${BASE}/genres`, {
      category: 'musicLibrary',
      message: 'Error loading genres',
      checkStatus: true,
      params: scoped(),
    });
    if (!inScope(scope)) return;
    if (result.ok && Array.isArray(result.data?.genres)) {
      // Alphabetical, skipping the empty-name genre Navidrome can emit.
      genres.value = result.data.genres
        .filter((g) => (g.value || '').trim())
        .sort((a, b) => a.value.localeCompare(b.value));
      genresLoaded.value = true;
    }
    genresLoading.value = false;
  }

  // =========================================================================
  // CATALOG — Playlists (getPlaylists, single call)
  // =========================================================================
  const playlists = ref([]);
  const playlistsLoading = ref(false);
  const playlistsLoaded = ref(false);

  async function loadPlaylists({ force = false } = {}) {
    if (playlistsLoaded.value && !force) return;
    const scope = activeLibraryId.value;
    playlistsLoading.value = true;
    const result = await apiCall.get(`${BASE}/playlists`, {
      category: 'musicLibrary',
      message: 'Error loading playlists',
      checkStatus: true,
      params: scoped(),
    });
    if (!inScope(scope)) return;
    if (result.ok && Array.isArray(result.data?.playlists)) {
      playlists.value = result.data.playlists;
      playlistsLoaded.value = true;
    }
    playlistsLoading.value = false;
  }

  // =========================================================================
  // PLAYLIST WRITES (create/rename/add/reorder-remove/delete). Navidrome owns
  // the data; every successful write refreshes the cached playlist list so the
  // tab (names + counts) stays truthful. Drill-down views hold their own copy
  // of a playlist's entries and pass the full new order to setPlaylistTracks.
  // =========================================================================
  async function createPlaylist(name, songIds = null) {
    // The storage space rides along: a playlist created empty has no track to be
    // placed by later, so this is the only moment it can be tied to one.
    const body = scoped(songIds?.length ? { name, song_ids: songIds } : { name });
    const result = await apiCall.post(`${BASE}/playlists`, body, {
      category: 'musicLibrary',
      message: 'Error creating playlist',
    });
    if (result.ok && result.data?.status === 'success') {
      await loadPlaylists({ force: true });
      return result.data.playlist || null;
    }
    return null;
  }

  // One edit per call — the backend accepts exactly one operation.
  async function editPlaylist(playlistId, payload) {
    const result = await apiCall.put(`${BASE}/playlist/${playlistId}`, payload, {
      category: 'musicLibrary',
      message: 'Error updating playlist',
    });
    const ok = result.ok && result.data?.status === 'success';
    if (ok && playlistsLoaded.value) await loadPlaylists({ force: true });
    return ok;
  }

  const renamePlaylist = (playlistId, name) => editPlaylist(playlistId, { name });
  const addToPlaylist = (playlistId, songIds) =>
    editPlaylist(playlistId, { song_ids_to_add: songIds });
  const setPlaylistTracks = (playlistId, trackIds) =>
    editPlaylist(playlistId, { track_ids: trackIds });

  async function deletePlaylist(playlistId) {
    const result = await apiCall.delete(`${BASE}/playlist/${playlistId}`, {
      category: 'musicLibrary',
      message: 'Error deleting playlist',
    });
    if (result.ok && result.data?.status === 'success') {
      await loadPlaylists({ force: true });
      return true;
    }
    return false;
  }

  // Add-to-playlist picker, hosted once at the source root and opened from any
  // track row's ⋯ menu. null = closed; an array of song ids = open for those.
  const addToPlaylistSongIds = ref(null);
  function requestAddToPlaylist(songIds) {
    if (songIds?.length) addToPlaylistSongIds.value = songIds;
  }
  function closeAddToPlaylist() {
    addToPlaylistSongIds.value = null;
  }

  // =========================================================================
  // CATALOG — single-item fetches (drill-down views hold their own result)
  // =========================================================================
  async function fetchAlbum(albumId) {
    const result = await apiCall.get(`${BASE}/album/${albumId}`, {
      category: 'musicLibrary',
      message: 'Error loading album',
    });
    return result.ok ? result.data?.album || null : null;
  }

  async function fetchArtist(artistId) {
    const result = await apiCall.get(`${BASE}/artist/${artistId}`, {
      category: 'musicLibrary',
      message: 'Error loading artist',
    });
    return result.ok ? result.data?.artist || null : null;
  }

  async function fetchGenreSongs(genre) {
    const result = await apiCall.get(`${BASE}/genre-songs`, {
      category: 'musicLibrary',
      message: 'Error loading genre',
      params: scoped({ genre, count: 500 }),
    });
    return result.ok ? result.data?.songs || [] : [];
  }

  async function fetchGenreAlbums(genre) {
    const result = await apiCall.get(`${BASE}/albums`, {
      category: 'musicLibrary',
      message: 'Error loading genre',
      params: scoped({ type: 'byGenre', genre, size: 500 }),
    });
    return result.ok ? result.data?.albums || [] : [];
  }

  async function fetchPlaylist(playlistId) {
    const result = await apiCall.get(`${BASE}/playlist/${playlistId}`, {
      category: 'musicLibrary',
      message: 'Error loading playlist',
    });
    return result.ok ? result.data?.playlist || null : null;
  }

  // =========================================================================
  // SEARCH (search3 across artists/albums/songs), state persisted across nav
  // =========================================================================
  const searchTerm = ref('');
  const lastSearchTerm = ref('');
  const searchResults = ref({ artists: [], albums: [], songs: [] });
  const searchLoading = ref(false);
  const hasSearched = ref(false);

  async function search() {
    const query = searchTerm.value.trim();
    if (!query) {
      clearSearch();
      return;
    }
    const scope = activeLibraryId.value;
    searchLoading.value = true;
    const result = await apiCall.get(`${BASE}/search`, {
      category: 'musicLibrary',
      message: 'Error searching library',
      checkStatus: true,
      params: scoped({ query }),
    });
    if (!inScope(scope)) return;
    if (result.ok) {
      searchResults.value = {
        artists: result.data?.artists || [],
        albums: result.data?.albums || [],
        songs: result.data?.songs || [],
      };
      hasSearched.value = true;
      lastSearchTerm.value = query;
    }
    searchLoading.value = false;
  }

  function clearSearch() {
    searchTerm.value = '';
    lastSearchTerm.value = '';
    searchResults.value = { artists: [], albums: [], songs: [] };
    hasSearched.value = false;
    searchLoading.value = false;
  }

  const searchEmpty = computed(() =>
    !searchResults.value.artists.length &&
    !searchResults.value.albums.length &&
    !searchResults.value.songs.length
  );

  // =========================================================================
  // SCAN STATUS (polled/resynced; drives the "building library…" empty state)
  // =========================================================================
  const scanStatus = ref(null); // { scanning, count, folderCount } | null

  async function refreshScanStatus() {
    const result = await apiCall.get(`${BASE}/scan-status`, {
      category: 'musicLibrary',
      message: 'Error loading scan status',
      checkStatus: true,
      logLevel: 'debug',
    });
    if (result.ok) {
      scanStatus.value = result.data?.scan_status || null;
    }
  }

  const isScanning = computed(() => !!scanStatus.value?.scanning);
  // Tracks indexed so far — surfaced in the "building library…" state as live
  // progress during a fresh scan.
  const scanCount = computed(() => scanStatus.value?.count || 0);

  // On-demand rescan ("I added music, refresh now"). The watcher can't see
  // changes made on a NAS over CIFS/NFS, so this forces Navidrome to re-index.
  async function rescan() {
    const result = await apiCall.post(`${BASE}/scan`, null, {
      category: 'musicLibrary',
      message: 'Error starting library scan',
    });
    if (result.ok && result.data?.status === 'success') {
      await refreshScanStatus();
      return true;
    }
    return false;
  }

  // Manual refresh: a full scan (indexes new music + purges gone files) when every
  // share is mounted, else a quick scan — 'blocked' returns the offline shares that
  // deferred the cleanup (a full scan would drop their still-valid tracks).
  async function refreshLibrary() {
    const full = await apiCall.post(`${BASE}/scan/full`, null, {
      category: 'musicLibrary',
      message: 'Error refreshing library',
    });
    if (full.ok && full.data?.status === 'success') {
      await refreshScanStatus();
      return { ok: true };
    }
    if (full.ok && full.data?.status === 'blocked') {
      const ok = await rescan();
      return { ok, offlineShares: full.data.offline_shares || [] };
    }
    return { ok: false };
  }

  // A scan (manual refresh, or triggered by a share add/remove) just finished —
  // the catalog caches below are stale (new tracks missing, gone ones still
  // listed) until something reloads them. No WS event marks scan completion, so
  // this is driven by refreshScanStatus() polling flipping isScanning off; only
  // refresh whichever lists are already loaded, same set resync() touches.
  watch(isScanning, async (scanning, wasScanning) => {
    if (scanning || !wasScanning) return;
    // Storages first and awaited: a scan can create or retire a library (a share
    // that came online, a key that was just indexed), and refetching before that
    // lands would scope every call below to a library that is on its way out.
    await loadStorages({ force: true });
    if (albumsLoaded.value) loadAlbums({ reset: true });
    if (artistsLoaded.value) loadArtists({ force: true });
    if (genresLoaded.value) loadGenres({ force: true });
    if (playlistsLoaded.value) loadPlaylists({ force: true });
  });

  // Switching storage space invalidates EVERY cached catalog list, playlists and
  // liked songs included: a playlist belongs to the storage space it was created
  // in, and a favourite to the space its track lives on, so nothing here carries
  // over. Whatever was loaded is refetched, so the tab the user is looking at
  // repopulates without a second interaction.
  watch(activeLibraryId, () => {
    const reload = {
      albums: albumsLoaded.value,
      artists: artistsLoaded.value,
      genres: genresLoaded.value,
      playlists: playlistsLoaded.value,
      liked: likedSongsLoaded.value,
    };
    albums.value = [];
    albumsLoaded.value = false;
    albumsHasMore.value = true;
    artistIndex.value = [];
    artistsLoaded.value = false;
    genres.value = [];
    genresLoaded.value = false;
    playlists.value = [];
    playlistsLoaded.value = false;
    likedSongs.value = [];
    likedSongIds.value = new Set();
    likedSongsLoaded.value = false;
    clearSearch();
    if (reload.albums) loadAlbums();
    if (reload.artists) loadArtists();
    if (reload.genres) loadGenres();
    if (reload.playlists) loadPlaylists();
    if (reload.liked) loadLikedSongs();
  });

  // =========================================================================
  // NETWORK SHARES (SMB/NFS) — configured in Settings; the backend persists,
  // (re)mounts read-only under /media/milo, and rescans on every write. Non-
  // secret metadata only ({id, type, host, path, name, has_credentials}); the
  // password is write-only (handed to the mount helper, never read back).
  // =========================================================================
  const shares = ref([]);
  const sharesLoaded = ref(false);

  async function loadShares({ force = false } = {}) {
    if (sharesLoaded.value && !force) return;
    const result = await apiCall.get(`${BASE}/shares`, {
      category: 'musicLibrary',
      message: 'Error loading network shares',
    });
    if (result.ok && Array.isArray(result.data?.shares)) {
      shares.value = result.data.shares;
      sharesLoaded.value = true;
    }
  }

  // Adding/updating a share (re)mounts it and kicks a rescan; refresh the scan
  // status so the library view reflects the "building…" state. Returns
  // { ok, mounted, error }: `ok` means the config was saved, `mounted` whether
  // the read-only mount actually succeeded (a share persists either way, but the
  // UI tells the user whether it connected). No throw — the form reads the flags.
  async function addShare(payload) {
    const result = await apiCall.post(`${BASE}/shares`, payload, {
      category: 'musicLibrary',
      message: 'Error adding network share',
    });
    if (result.ok && result.data?.status === 'success') {
      await loadShares({ force: true });
      refreshScanStatus();
      return { ok: true, mounted: !!result.data.share?.mounted };
    }
    return { ok: false, error: result.error?.detail };
  }

  async function updateShare(shareId, payload) {
    const result = await apiCall.put(`${BASE}/shares/${shareId}`, payload, {
      category: 'musicLibrary',
      message: 'Error updating network share',
    });
    if (result.ok && result.data?.status === 'success') {
      await loadShares({ force: true });
      refreshScanStatus();
      return { ok: true, mounted: !!result.data.share?.mounted };
    }
    return { ok: false, error: result.error?.detail };
  }

  async function removeShare(shareId) {
    const result = await apiCall.delete(`${BASE}/shares/${shareId}`, {
      category: 'musicLibrary',
      message: 'Error removing network share',
    });
    if (result.ok && result.data?.status === 'success') {
      await loadShares({ force: true });
      refreshScanStatus();
      return true;
    }
    return false;
  }

  // =========================================================================
  // USB DEVICES (read-only status). The backend auto-mounts keys on hotplug;
  // this is surfaced only so the settings screen can show whether a key is
  // plugged in, beside the configurable network shares. No WS event — fetched
  // when the settings screen mounts and refreshed on resync.
  // =========================================================================
  const usbDevices = ref([]);
  const usbLoaded = ref(false);

  async function loadUsbDevices() {
    const result = await apiCall.get(`${BASE}/usb-devices`, {
      category: 'musicLibrary',
      message: 'Error loading USB devices',
      logLevel: 'debug',
    });
    if (result.ok && Array.isArray(result.data?.devices)) {
      usbDevices.value = result.data.devices;
      usbLoaded.value = true;
    }
  }

  // Name a plugged-in USB key. The name is stored against its filesystem UUID,
  // so it comes back with the key; an empty name restores the disk label.
  async function renameUsbDevice(uuid, name) {
    // Encoded: a filesystem UUID is URL-safe, but the fallback identity for a
    // key without one is a kernel device name, and an unescaped one would match
    // no route.
    const result = await apiCall.put(`${BASE}/usb-devices/${encodeURIComponent(uuid)}`, { name }, {
      category: 'musicLibrary',
      message: 'Error renaming USB device',
    });
    if (result.ok && result.data?.status === 'success') {
      await Promise.all([loadUsbDevices(), loadStorages({ force: true })]);
      return true;
    }
    return false;
  }

  // mDNS discovery of SMB/NFS servers on the LAN (a convenience to prefill the
  // add-share form). Resilient: an empty list simply means "type it manually".
  async function discoverServers() {
    const result = await apiCall.get(`${BASE}/shares/discover`, {
      category: 'musicLibrary',
      message: 'Error discovering network servers',
      checkStatus: true,
      logLevel: 'debug',
    });
    return result.ok && Array.isArray(result.data?.servers) ? result.data.servers : [];
  }

  // Walk a server one level for the add-share wizard (SMB shares/folders, NFS
  // exports) WITHOUT mounting. Returns the backend's typed envelope
  // { status: 'ok'|'auth_required'|'unreachable'|'error', entries, message } —
  // credentials are only sent for CIFS and are never persisted client-side.
  async function browseShare({ type, host, path = '', username, password, domain }) {
    const body = { type, host, path };
    if (type === 'cifs' && (username || password)) {
      Object.assign(body, { username, password, domain });
    }
    const result = await apiCall.post(`${BASE}/shares/browse`, body, {
      category: 'musicLibrary',
      message: 'Error browsing network server',
      checkStatus: true,
      logLevel: 'debug',
    });
    if (result.ok && result.data?.status) {
      return {
        status: result.data.status,
        entries: Array.isArray(result.data.entries) ? result.data.entries : [],
        message: result.data.message || '',
      };
    }
    return { status: 'error', entries: [], message: '' };
  }

  // =========================================================================
  // UI STATE (persisted across navigation)
  // =========================================================================
  const activeTab = ref('albums'); // albums | artists | genres | playlists

  // =========================================================================
  // RESYNC (App.vue reconnect / tab-visible). Refresh scan status (a scan that
  // finished while backgrounded) and re-pull whichever top-level lists are
  // already cached, without clearing what the user is currently looking at.
  // =========================================================================
  async function resync() {
    // Storages first and ALONE: a key plugged in (or pulled) while the tab was
    // backgrounded changes which library the calls below scope to, and issuing
    // them in the same batch would send the old id — those responses land after
    // the switch and write another storage's catalog into the store.
    await loadStorages({ force: true });
    const tasks = [refreshScanStatus()];
    if (albumsLoaded.value) tasks.push(loadAlbums({ reset: true }));
    if (artistsLoaded.value) tasks.push(loadArtists({ force: true }));
    if (genresLoaded.value) tasks.push(loadGenres({ force: true }));
    if (playlistsLoaded.value) tasks.push(loadPlaylists({ force: true }));
    if (likedSongsLoaded.value) tasks.push(loadLikedSongs({ force: true }));
    if (sharesLoaded.value) tasks.push(loadShares({ force: true }));
    if (usbLoaded.value) tasks.push(loadUsbDevices());
    await Promise.allSettled(tasks);
  }

  return {
    resync,

    // Helpers
    coverUrl,
    thumbUrl,
    gridUrl,

    // Now playing
    nowPlaying,
    displayTrack,
    clearDisplayTrack,
    queue,
    queueIndex,
    shuffle,
    currentTrackId,
    isPlaying,

    // Favorites (liked songs)
    likedSongs,
    likedSongIds,
    likedSongsCount,
    loadLikedSongs,
    setSongFavorite,
    currentStarred,
    toggleCurrentStar,

    // Transport
    playContext,
    playIndex,
    pause,
    resume,
    next,
    previous,
    swipePrevious,
    stop,
    toggleShuffle,

    // Albums
    albums,
    albumsLoading,
    albumsLoaded,
    albumsHasMore,
    loadAlbums,
    loadMoreAlbums,

    // Artists
    displayedArtistIndex,
    artistsHasMore,
    renderMoreArtists,
    artistsLoading,
    artistsLoaded,
    loadArtists,

    // Genres
    genres,
    genresLoading,
    genresLoaded,
    loadGenres,

    // Playlists
    playlists,
    playlistsLoading,
    playlistsLoaded,
    loadPlaylists,
    createPlaylist,
    renamePlaylist,
    addToPlaylist,
    setPlaylistTracks,
    deletePlaylist,
    addToPlaylistSongIds,
    requestAddToPlaylist,
    closeAddToPlaylist,

    // Single-item fetches
    fetchAlbum,
    fetchArtist,
    fetchGenreSongs,
    fetchGenreAlbums,
    fetchPlaylist,

    // Search
    searchTerm,
    lastSearchTerm,
    searchResults,
    searchLoading,
    hasSearched,
    searchEmpty,
    search,
    clearSearch,

    // Scan status
    scanStatus,
    isScanning,
    scanCount,
    refreshScanStatus,
    refreshLibrary,

    // Network shares
    shares,
    loadShares,
    addShare,
    updateShare,
    removeShare,
    discoverServers,
    browseShare,

    // USB devices
    usbDevices,
    loadUsbDevices,
    renameUsbDevice,

    // Storage spaces (the catalog scope)
    storages,
    browsableStorages,
    activeLibraryId,
    loadStorages,

    // UI
    activeTab,
  };
});
