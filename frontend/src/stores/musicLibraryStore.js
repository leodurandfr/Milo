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
import { defineStore } from 'pinia';
import { ref, computed, watch } from 'vue';
import { apiCall } from '@/services/apiCall';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';

const BASE = '/api/music-library';
const ALBUMS_PAGE_SIZE = 40;
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
  const isBuffering = computed(() => !!meta.value.is_buffering);

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
  // FAVORITES (star) — optimistic local overrides over payload `starred`
  // =========================================================================
  // key `${kind}:${id}` → boolean. Absent means "trust the payload's starred".
  const starOverrides = ref({});
  const starKey = (kind, id) => `${kind}:${id}`;

  function isStarred(kind, id, rawStarred) {
    const k = starKey(kind, id);
    if (Object.prototype.hasOwnProperty.call(starOverrides.value, k)) {
      return starOverrides.value[k];
    }
    return !!rawStarred;
  }

  async function toggleStar(kind, id, rawStarred) {
    if (!id) return false;
    const current = isStarred(kind, id, rawStarred);
    const next = !current;
    starOverrides.value = { ...starOverrides.value, [starKey(kind, id)]: next };

    const result = await apiCall.post(`${BASE}/${next ? 'star' : 'unstar'}`,
      { id, kind }, {
        category: 'musicLibrary',
        message: `Error ${next ? 'starring' : 'unstarring'} item`,
      });

    if (!result.ok || result.data?.status !== 'success') {
      // Revert the optimistic flip on failure.
      starOverrides.value = { ...starOverrides.value, [starKey(kind, id)]: current };
      return false;
    }
    return true;
  }

  // Star state of the currently-playing track (for the docked player heart).
  const currentStarred = computed(() => {
    const song = queue.value[queueIndex.value];
    return isStarred('song', currentTrackId.value, song?.starred);
  });
  function toggleCurrentStar() {
    const song = queue.value[queueIndex.value];
    return toggleStar('song', currentTrackId.value, song?.starred);
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
    albumsLoading.value = true;
    if (reset) {
      albums.value = [];
      albumsHasMore.value = true;
    }
    const result = await apiCall.get(`${BASE}/albums`, {
      category: 'musicLibrary',
      message: 'Error loading albums',
      checkStatus: true,
      params: { type: 'alphabeticalByName', size: ALBUMS_PAGE_SIZE, offset: 0 },
    });
    if (result.ok && Array.isArray(result.data?.albums)) {
      albums.value = result.data.albums;
      albumsHasMore.value = result.data.albums.length >= ALBUMS_PAGE_SIZE;
      albumsLoaded.value = true;
    }
    albumsLoading.value = false;
  }

  async function loadMoreAlbums() {
    if (albumsLoading.value || !albumsHasMore.value) return;
    albumsLoading.value = true;
    const result = await apiCall.get(`${BASE}/albums`, {
      category: 'musicLibrary',
      message: 'Error loading more albums',
      checkStatus: true,
      params: { type: 'alphabeticalByName', size: ALBUMS_PAGE_SIZE, offset: albums.value.length },
    });
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

  async function loadArtists({ force = false } = {}) {
    if (artistsLoaded.value && !force) return;
    artistsLoading.value = true;
    const result = await apiCall.get(`${BASE}/artists`, {
      category: 'musicLibrary',
      message: 'Error loading artists',
      checkStatus: true,
    });
    if (result.ok && Array.isArray(result.data?.index)) {
      artistIndex.value = result.data.index;
      artistsLoaded.value = true;
    }
    artistsLoading.value = false;
  }

  // =========================================================================
  // CATALOG — Genres (getGenres, single call)
  // =========================================================================
  const genres = ref([]);
  const genresLoading = ref(false);
  const genresLoaded = ref(false);

  async function loadGenres({ force = false } = {}) {
    if (genresLoaded.value && !force) return;
    genresLoading.value = true;
    const result = await apiCall.get(`${BASE}/genres`, {
      category: 'musicLibrary',
      message: 'Error loading genres',
      checkStatus: true,
    });
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
    playlistsLoading.value = true;
    const result = await apiCall.get(`${BASE}/playlists`, {
      category: 'musicLibrary',
      message: 'Error loading playlists',
      checkStatus: true,
    });
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
    const body = songIds?.length ? { name, song_ids: songIds } : { name };
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
      params: { genre, count: 500 },
    });
    return result.ok ? result.data?.songs || [] : [];
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
    searchLoading.value = true;
    const result = await apiCall.get(`${BASE}/search`, {
      category: 'musicLibrary',
      message: 'Error searching library',
      checkStatus: true,
      params: { query },
    });
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

  // =========================================================================
  // NETWORK SHARES (SMB/NFS) — configured in Settings; the backend persists,
  // (re)mounts read-only under /media/milo, and rescans on every write. Non-
  // secret metadata only ({id, type, host, path, name, has_credentials}); the
  // password is write-only (handed to the mount helper, never read back).
  // =========================================================================
  const shares = ref([]);
  const sharesLoading = ref(false);
  const sharesLoaded = ref(false);

  async function loadShares({ force = false } = {}) {
    if (sharesLoaded.value && !force) return;
    sharesLoading.value = true;
    const result = await apiCall.get(`${BASE}/shares`, {
      category: 'musicLibrary',
      message: 'Error loading network shares',
    });
    if (result.ok && Array.isArray(result.data?.shares)) {
      shares.value = result.data.shares;
      sharesLoaded.value = true;
    }
    sharesLoading.value = false;
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
    const tasks = [refreshScanStatus()];
    if (albumsLoaded.value) tasks.push(loadAlbums({ reset: true }));
    if (artistsLoaded.value) tasks.push(loadArtists({ force: true }));
    if (genresLoaded.value) tasks.push(loadGenres({ force: true }));
    if (playlistsLoaded.value) tasks.push(loadPlaylists({ force: true }));
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
    isBuffering,

    // Favorites
    isStarred,
    toggleStar,
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
    setShuffle,
    toggleShuffle,

    // Albums
    albums,
    albumsLoading,
    albumsLoaded,
    albumsHasMore,
    loadAlbums,
    loadMoreAlbums,

    // Artists
    artistIndex,
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
    sharesLoading,
    sharesLoaded,
    loadShares,
    addShare,
    updateShare,
    removeShare,
    discoverServers,
    browseShare,

    // USB devices (read-only)
    usbDevices,
    usbLoaded,
    loadUsbDevices,

    // UI
    activeTab,
  };
});
