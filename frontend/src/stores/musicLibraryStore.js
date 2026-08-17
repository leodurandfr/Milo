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
//      Navidrome library id — see backend libraries.py), unless the user has
//      turned that off (`settings.music_library.separate_storages`), in which
//      case activeLibraryId stays null and every read spans the lot. That
//      includes playlists and liked songs — Navidrome keeps both catalog-wide,
//      and the backend narrows them when a scope is given, because a playlist
//      spanning a NAS and a USB key is what the filter exists to prevent.
//
//   3. Storage spaces — pushed, not polled. `source/storages_changed` carries
//      the whole list (USB keys and shares alike, each with a live `mounted`
//      flag and its track/album counts) plus whether a Navidrome scan is
//      running. It arrives on every hotplug, share write and scan poll, so
//      plugging a key in or pulling it out reaches every open screen without a
//      refetch — and the settings rows and the library's storage filter are the
//      same list, so they cannot disagree.
import { defineStore } from 'pinia';
import { ref, computed, watch } from 'vue';
import { apiCall } from '@/services/apiCall';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useSettingsStore } from '@/stores/settingsStore';

const BASE = '/api/music-library';
const ALBUMS_PAGE_SIZE = 40;
// Artists arrive in one call, so this paces the RENDER, not the fetch.
const ARTISTS_RENDER_CHUNK = 40;
// Cover sizes (square max dimension Navidrome resizes to), scaled by
// devicePixelRatio (capped at 2) so covers stay crisp on HiDPI screens. The
// player uses the backend-provided full-size album_art_url as-is.
//   - grid: auto-fill never leaves a card wider than ~240px CSS, so the largest
//     cover ever painted is a 2-column phone at @3x — 569 physical px. 300 is
//     the SMALLEST base that still covers it (2 x 300 >= 569, the cap being 2).
//     Lower blurs large phones; higher only buys decoded bytes the kiosk pays
//     for in RAM, at ~230kB retained per cover scrolled past.
//   - row:  MediaRow / picker thumbnails render at ~60px CSS.
const _DPR = Math.min(window.devicePixelRatio || 1, 2);
const COVER_GRID_PX = Math.round(300 * _DPR);
const COVER_ROW_PX = Math.round(80 * _DPR);

export const useMusicLibraryStore = defineStore('musicLibrary', () => {
  const unifiedStore = useUnifiedAudioStore();
  const settingsStore = useSettingsStore();

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
  // The storage space being browsed, or null when the spaces are merged into one
  // catalog (the `separate_storages` setting) or none exists yet.
  const activeLibraryId = ref(null);

  // One tab per storage space, or all of them merged. A display choice, stored
  // on the backend so every screen agrees.
  const separateStorages = computed(
    () => settingsStore.musicLibrarySettings.separate_storages !== false
  );

  // Storage spaces that can actually be browsed right now: mounted, and with a
  // library Navidrome has accepted. An unplugged key stays in `storages` — it
  // keeps its library and its index, and the settings screen still lists it —
  // but there is nothing to browse in it.
  const browsableStorages = computed(() =>
    storages.value.filter((s) => s.library_id != null && s.mounted)
  );

  // The USB keys among them, for the settings screen. Same list, same names, so
  // a row and a filter button can never disagree about which key is which.
  const usbDevices = computed(() => storages.value.filter((s) => s.kind === 'usb'));

  // The pushed storage entry of each configured share, by id. The settings rows
  // read their mount state and track count from here rather than from the share
  // config, so an unreachable NAS greys out — and a growing catalog counts up —
  // the moment the backend notices, with no refetch.
  const shareStorages = computed(
    () => new Map(storages.value.filter((s) => s.kind === 'share').map((s) => [s.id, s]))
  );

  // The storage space the user is looking at that has just gone away. Kept
  // selected on purpose: it is what LibraryHome puts the "storage unplugged"
  // message in place of the grid for. Cleared by picking another space.
  const disconnectedStorage = computed(() => {
    if (activeLibraryId.value == null) return null;
    const active = storages.value.find((s) => s.library_id === activeLibraryId.value);
    return active && !active.mounted ? active : null;
  });

  // A storage space that is mounted and has a library, yet holds nothing but
  // tracks Navidrome has flagged as gone. The catalog is empty, but for the
  // opposite reason to "no music here yet": the files are right there and the
  // index disagrees, which only a rescan settles.
  //
  // This is worth naming because it is what a lost scan looks like from the UI,
  // and the generic empty state actively misleads there — it tells someone whose
  // NAS is mounted and full to go connect a NAS. A scan that misses a mount is
  // also invisible everywhere else: it self-heals on a dev machine (inotify sees
  // files appear locally) and only persists on the appliance, where storage
  // arrives by mount and no watcher event ever fires.
  const unindexedStorages = computed(() =>
    browsableStorages.value.filter((s) => !s.track_count && s.missing_count > 0)
  );

  // The one the current view is about: the space being browsed, or — when the
  // spaces are merged — any of them, since a merged catalog is only as complete
  // as all of its members.
  const unindexedStorage = computed(() => {
    if (activeLibraryId.value == null) return unindexedStorages.value[0] || null;
    return (
      unindexedStorages.value.find((s) => s.library_id === activeLibraryId.value) || null
    );
  });

  /**
   * Apply a storage picture — the WS push and the initial GET share this, so
   * both paths land identically.
   */
  function applyStorages({ storages: list, scanning }) {
    if (Array.isArray(list)) {
      storages.value = list;
      storagesLoaded.value = true;
    }
    if (typeof scanning === 'boolean') applyScanning(scanning);
  }

  async function loadStorages({ force = false } = {}) {
    if (storagesLoaded.value && !force) return;
    const result = await apiCall.get(`${BASE}/storages`, {
      category: 'musicLibrary',
      message: 'Error loading storage spaces',
      logLevel: 'debug',
    });
    if (result.ok) applyStorages(result.data || {});
  }

  /** WS: source/storages_changed — a hotplug, a share write, or a scan poll. */
  function handleStoragesEvent(event) {
    applyStorages(event.data || {});
  }

  // Keep the selection somewhere it makes sense. Merged mode has no selection at
  // all; otherwise the rule is "stay put if you still can". A space that is
  // merely unplugged deliberately KEEPS the selection: switching away on its own
  // would swap the view out from under the user with no explanation, where
  // holding it lets LibraryHome say the key was removed. Only a space that has
  // left the list entirely (share deleted, key forgotten) forces a move.
  watch([browsableStorages, separateStorages, storages], () => {
    if (!separateStorages.value) {
      activeLibraryId.value = null;
      return;
    }
    // `!= null` guard first: a storage space Navidrome has not accepted yet also
    // carries library_id null, and would otherwise match a null selection and
    // read as "still listed" — leaving nothing selected at all.
    const stillListed = activeLibraryId.value != null && storages.value.some(
      (s) => s.library_id === activeLibraryId.value
    );
    if (stillListed) return;
    activeLibraryId.value = browsableStorages.value[0]?.library_id ?? null;
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

  // Live now-playing, or null when the queue is cleared (READY).
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
  // queue metadata the instant it drops to READY, so binding the docked
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
      // The storage space these tracks came from. Subsonic song dicts carry no
      // library of their own, so this is the only thing that lets the backend
      // stop playback when this queue's USB key is pulled out.
      library_id: activeLibraryId.value,
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
    // The flag is released in `finally`, ahead of the scope check: a response
    // that lands out of scope is dropped, but the skeleton grid it left up is
    // gated on the flag and nothing would come back to lower it.
    let result;
    try {
      result = await apiCall.get(`${BASE}/albums`, {
        category: 'musicLibrary',
        message: 'Error loading albums',
        checkStatus: true,
        params: scoped({ type: 'alphabeticalByName', size: ALBUMS_PAGE_SIZE, offset: 0 }),
      });
    } finally {
      albumsLoading.value = false;
    }
    if (!inScope(scope)) return;
    if (result.ok && Array.isArray(result.data?.albums)) {
      albums.value = result.data.albums;
      albumsHasMore.value = result.data.albums.length >= ALBUMS_PAGE_SIZE;
      albumsLoaded.value = true;
    }
  }

  async function loadMoreAlbums() {
    if (albumsLoading.value || !albumsHasMore.value) return;
    const scope = activeLibraryId.value;
    albumsLoading.value = true;
    let result;
    try {
      result = await apiCall.get(`${BASE}/albums`, {
        category: 'musicLibrary',
        message: 'Error loading more albums',
        checkStatus: true,
        params: scoped({
          type: 'alphabeticalByName',
          size: ALBUMS_PAGE_SIZE,
          offset: albums.value.length,
        }),
      });
    } finally {
      albumsLoading.value = false;
    }
    if (!inScope(scope)) return;
    if (result.ok && Array.isArray(result.data?.albums)) {
      albums.value = [...albums.value, ...result.data.albums];
      albumsHasMore.value = result.data.albums.length >= ALBUMS_PAGE_SIZE;
    }
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
    let result;
    try {
      result = await apiCall.get(`${BASE}/artists`, {
        category: 'musicLibrary',
        message: 'Error loading artists',
        checkStatus: true,
        params: scoped(),
      });
    } finally {
      artistsLoading.value = false;
    }
    if (!inScope(scope)) return;
    if (result.ok && Array.isArray(result.data?.index)) {
      artistIndex.value = result.data.index;
      artistsRendered.value = ARTISTS_RENDER_CHUNK;
      artistsLoaded.value = true;
    }
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
    let result;
    try {
      result = await apiCall.get(`${BASE}/genres`, {
        category: 'musicLibrary',
        message: 'Error loading genres',
        checkStatus: true,
        params: scoped(),
      });
    } finally {
      genresLoading.value = false;
    }
    if (!inScope(scope)) return;
    if (result.ok && Array.isArray(result.data?.genres)) {
      // Alphabetical, skipping the empty-name genre Navidrome can emit.
      genres.value = result.data.genres
        .filter((g) => (g.value || '').trim())
        .sort((a, b) => a.value.localeCompare(b.value));
      genresLoaded.value = true;
    }
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
    let result;
    try {
      result = await apiCall.get(`${BASE}/playlists`, {
        category: 'musicLibrary',
        message: 'Error loading playlists',
        checkStatus: true,
        params: scoped(),
      });
    } finally {
      playlistsLoading.value = false;
    }
    if (!inScope(scope)) return;
    if (result.ok && Array.isArray(result.data?.playlists)) {
      playlists.value = result.data.playlists;
      playlistsLoaded.value = true;
    }
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

  // Which playlists already hold every one of these songs, for the picker's
  // checkmarks. One request: the backend owns the fan-out, so the storage scope
  // is resolved once instead of once per playlist. Nothing is cached — the
  // answer must be fresh, and a stale checkmark re-adds a track.
  async function fetchPlaylistsContaining(songIds) {
    if (!songIds?.length) return new Set();
    // URLSearchParams rather than a plain object: axios encodes an array value
    // as `song_id[]=`, which FastAPI's repeated-Query binding rejects with 422.
    const params = new URLSearchParams(scoped());
    songIds.forEach((id) => params.append('song_id', id));
    const result = await apiCall.get(`${BASE}/playlists/containing`, {
      category: 'musicLibrary',
      message: 'Error checking playlist membership',
      params,
    });
    return new Set(result.ok ? result.data?.playlist_ids || [] : []);
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
  // SCAN STATE (pushed with the storage list; drives the "building…" state)
  // =========================================================================
  const scanning = ref(false);

  function applyScanning(value) {
    scanning.value = value;
  }

  const isScanning = computed(() => scanning.value);

  // Tracks indexed in the storage space on screen. This is the honest progress
  // figure: Navidrome's global scan status reports a `count` that does NOT move
  // until a scan ends (it read 2419 — the previous scan's total — for all 18
  // minutes it took to index a 10 000-track iPod, then jumped), whereas the
  // per-library totals behind it grow as folders land. Merged view has no one
  // space to count, so it sums them.
  const activeStorageTrackCount = computed(() => {
    if (activeLibraryId.value == null) {
      return storages.value.reduce((n, s) => n + (s.track_count || 0), 0);
    }
    const active = storages.value.find((s) => s.library_id === activeLibraryId.value);
    return active?.track_count || 0;
  });

  // On-demand rescan ("I added music, refresh now"). The watcher can't see
  // changes made on a NAS over CIFS/NFS, so this forces Navidrome to re-index.
  // The scan flag comes back on the storages push, so nothing is polled here.
  async function rescan() {
    const result = await apiCall.post(`${BASE}/scan`, null, {
      category: 'musicLibrary',
      message: 'Error starting library scan',
    });
    return result.ok && result.data?.status === 'success';
  }

  // Manual refresh: a full scan (indexes new music + purges gone files) when every
  // storage space is mounted, else a quick scan — 'blocked' returns the spaces
  // that deferred the cleanup (a full scan would drop their still-valid tracks,
  // including an unplugged key's whole index).
  async function refreshLibrary() {
    const full = await apiCall.post(`${BASE}/scan/full`, null, {
      category: 'musicLibrary',
      message: 'Error refreshing library',
    });
    if (full.ok && full.data?.status === 'success') return { ok: true };
    if (full.ok && full.data?.status === 'blocked') {
      const ok = await rescan();
      return { ok, offlineShares: full.data.offline_shares || [] };
    }
    return { ok: false };
  }

  /** Refetch whichever top-level lists are already cached, in the current scope. */
  function reloadCachedLists() {
    if (albumsLoaded.value) loadAlbums({ reset: true });
    if (artistsLoaded.value) loadArtists({ force: true });
    if (genresLoaded.value) loadGenres({ force: true });
    if (playlistsLoaded.value) loadPlaylists({ force: true });
  }

  // A scan just finished — every cached list is stale (new tracks missing, gone
  // ones still listed). The storages push that flips the flag also carries the
  // new library set, so there is nothing to refetch before this runs.
  watch(isScanning, (now, before) => {
    if (now || !before) return;
    reloadCachedLists();
  });

  // …and while it runs, the storage space on screen fills as it is indexed.
  // Driven by its album count rather than a timer: the count only moves when
  // Navidrome has actually committed albums, so this refetches exactly when
  // there is something new to show, and never on an idle poll.
  const activeStorageAlbumCount = computed(() => {
    const active = storages.value.find((s) => s.library_id === activeLibraryId.value);
    return active?.album_count ?? null;
  });
  watch(activeStorageAlbumCount, (now, before) => {
    if (!isScanning.value || now == null || before == null || now === before) return;
    reloadCachedLists();
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
  // Config only. Whether a share is mounted right now comes from the storage
  // push (`mountedShareIds`), not from this payload's snapshot — that is what
  // makes a NAS going offline grey its row out without a refetch.
  const shareConfigs = ref([]);
  const sharesLoaded = ref(false);

  const shares = computed(() =>
    shareConfigs.value.map((share) => {
      const storage = shareStorages.value.get(share.id);
      return {
        ...share,
        mounted: !!storage?.mounted,
        track_count: storage?.track_count || 0,
      };
    })
  );

  async function loadShares({ force = false } = {}) {
    if (sharesLoaded.value && !force) return;
    const result = await apiCall.get(`${BASE}/shares`, {
      category: 'musicLibrary',
      message: 'Error loading network shares',
    });
    if (result.ok && Array.isArray(result.data?.shares)) {
      shareConfigs.value = result.data.shares;
      sharesLoaded.value = true;
    }
  }

  // Adding/updating a share (re)mounts it and kicks a rescan; the storage push
  // carries the new mount state and the "building…" flag, so only the config
  // needs refetching here. Returns { ok, mounted, error }: `ok` means the config
  // was saved, `mounted` whether the read-only mount actually succeeded (a share
  // persists either way, but the UI tells the user whether it connected). No
  // throw — the form reads the flags.
  async function addShare(payload) {
    const result = await apiCall.post(`${BASE}/shares`, payload, {
      category: 'musicLibrary',
      message: 'Error adding network share',
    });
    if (result.ok && result.data?.status === 'success') {
      await loadShares({ force: true });
      return {
        ok: true,
        mounted: !!result.data.share?.mounted,
        // The wizard follows this share's own indexing from here.
        shareId: result.data.share?.id || null,
      };
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
      return true;
    }
    return false;
  }

  // =========================================================================
  // USB KEYS — writes only. The list itself is `usbDevices` up top, a view over
  // the pushed storage spaces, so a key appearing or going reaches the settings
  // screen on the same event the library filter reads. Both writes rely on that
  // push for their refresh: the backend reconciles, then broadcasts.
  // =========================================================================

  // A filesystem UUID is URL-safe, but the fallback identity for a key without
  // one is a kernel device name — encode so it still matches the route.
  const usbPath = (uuid) => `${BASE}/usb-devices/${encodeURIComponent(uuid)}`;

  // Name a known USB key. The name is stored against its filesystem UUID, so it
  // comes back with the key; an empty name restores the disk label.
  async function renameUsbDevice(uuid, name) {
    const result = await apiCall.put(usbPath(uuid), { name }, {
      category: 'musicLibrary',
      message: 'Error renaming USB device',
    });
    return result.ok && result.data?.status === 'success';
  }

  // Forget an unplugged key: drops its Navidrome library and its index, which is
  // the only way a key that will never come back stops holding catalog rows.
  async function forgetUsbDevice(uuid) {
    const result = await apiCall.delete(usbPath(uuid), {
      category: 'musicLibrary',
      message: 'Error forgetting USB device',
    });
    return result.ok && result.data?.status === 'success';
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
  // RESYNC (App.vue reconnect / tab-visible). The storage list and the scan flag
  // are WS deltas, so every push missed while backgrounded is gone for good —
  // refetch them, then re-pull whichever top-level lists are already cached,
  // without clearing what the user is currently looking at.
  // =========================================================================
  async function resync() {
    // Storages first and ALONE: a key plugged in (or pulled) while the tab was
    // backgrounded changes which library the calls below scope to, and issuing
    // them in the same batch would send the old id — those responses land after
    // the switch and write another storage's catalog into the store. It also
    // carries the scan flag, so it heals a scan that started or ended meanwhile.
    await loadStorages({ force: true });
    const tasks = [];
    if (albumsLoaded.value) tasks.push(loadAlbums({ reset: true }));
    if (artistsLoaded.value) tasks.push(loadArtists({ force: true }));
    if (genresLoaded.value) tasks.push(loadGenres({ force: true }));
    if (playlistsLoaded.value) tasks.push(loadPlaylists({ force: true }));
    if (likedSongsLoaded.value) tasks.push(loadLikedSongs({ force: true }));
    if (sharesLoaded.value) tasks.push(loadShares({ force: true }));
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
    fetchPlaylistsContaining,

    // Search
    searchTerm,
    lastSearchTerm,
    searchResults,
    searchLoading,
    hasSearched,
    searchEmpty,
    search,
    clearSearch,

    // Scan state (pushed with the storage list)
    isScanning,
    activeStorageTrackCount,
    refreshLibrary,
    rescan,

    // Network shares
    shares,
    loadShares,
    addShare,
    updateShare,
    removeShare,
    discoverServers,
    browseShare,

    // USB keys (the list is a view over `storages`)
    usbDevices,
    renameUsbDevice,
    forgetUsbDevice,

    // Storage spaces (the catalog scope)
    storages,
    browsableStorages,
    separateStorages,
    disconnectedStorage,
    unindexedStorage,
    activeLibraryId,
    loadStorages,
    handleStoragesEvent,

    // UI
    activeTab,
  };
});
