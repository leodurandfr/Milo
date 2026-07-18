// frontend/src/stores/musicLibraryStore.js
//
// Central state for the Music Library source (Family C). Two concerns:
//
//   1. Now-playing — DERIVED from the central audio mirror
//      (unifiedAudioStore.systemState.metadata) gated on active_source ===
//      'music_library', exactly like cdStore. The backend broadcasts the queue
//      projection (title/artist/album/art + queue/index/shuffle/repeat) as
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
// Cover thumbnails in grids/rows — Navidrome resizes; the player uses the
// backend-provided full-size album_art_url as-is.
const COVER_THUMB_PX = 300;

export const useMusicLibraryStore = defineStore('musicLibrary', () => {
  const unifiedStore = useUnifiedAudioStore();

  // Proxy URL for a Navidrome cover id (album/song coverArt id). size omitted
  // → original bytes.
  function coverUrl(coverId, size = null) {
    if (!coverId) return '';
    return size ? `${BASE}/cover/${coverId}?size=${size}` : `${BASE}/cover/${coverId}`;
  }
  function thumbUrl(coverId) {
    return coverUrl(coverId, COVER_THUMB_PX);
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
  const repeat = computed(() => meta.value.repeat || 'off');
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
  const stop = () => send('stop');

  // =========================================================================
  // CATALOG — Albums (home Albums tab; getAlbumList2 newest, paged)
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
      params: { type: 'newest', size: ALBUMS_PAGE_SIZE, offset: 0 },
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
      params: { type: 'newest', size: ALBUMS_PAGE_SIZE, offset: albums.value.length },
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
    await Promise.allSettled(tasks);
  }

  return {
    resync,

    // Helpers
    coverUrl,
    thumbUrl,

    // Now playing
    nowPlaying,
    displayTrack,
    clearDisplayTrack,
    queue,
    queueIndex,
    shuffle,
    repeat,
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
    stop,

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
    refreshScanStatus,

    // UI
    activeTab,
  };
});
