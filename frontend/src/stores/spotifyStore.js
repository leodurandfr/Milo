import { defineStore } from 'pinia';
import { ref, computed, watch } from 'vue';
import { apiCall } from '@/services/apiCall';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { i18n } from '@/services/i18n';
import { bcp47For } from '@/constants/countries';
import { remoteRecordOf } from '@/utils/nowPlayingMetadata';

const BASE = '/api/spotify';
// A listing request answers as soon as more tracks are described, else after
// about 6 s: 20 answers in a row bringing nothing is two minutes of a listing
// that stopped moving, which no measured one came near (1159 tracks took 12 s
// to describe whole).
const MAX_IDLE_ROUNDS = 20;
// The answer the library routes give while nobody is signed in to the daemon
// (between a session end and the stored account signing back in, or with no
// account at all): a state the browser draws, not a failure.
const NOT_SIGNED_IN = 409;

/**
 * The Spotify browser: what the signed-in account's library holds, and the
 * profiles Milō keeps. What plays — and whose library it is — is derived from
 * the audio state (`details.spotify`), never fetched.
 */
export const useSpotifyStore = defineStore('spotify', () => {
  const unifiedStore = useUnifiedAudioStore();

  // =========================================================================
  // NOW PLAYING (derived from the central mirror)
  // =========================================================================
  const selected = computed(() =>
    unifiedStore.systemState.source === 'spotify' ? unifiedStore.systemState : null
  );
  const details = computed(() =>
    selected.value?.details?.kind === 'spotify' ? selected.value.details : null
  );
  const session = computed(() => selected.value?.session ?? null);

  const account = computed(() => details.value?.account ?? null);
  const signingIn = computed(() => !!details.value?.signing_in);
  const phase = computed(() => session.value?.phase ?? null);
  const isPlaying = computed(() => phase.value === 'playing');
  const currentTrackUri = computed(() => details.value?.track_uri ?? null);
  const currentContextUri = computed(() => details.value?.context_uri ?? null);

  // The track a session plays. Spotify keeps no resume point of its own (the
  // daemon keeps its context), so nothing shows once the session has ended.
  const nowPlaying = computed(() => {
    const live = session.value;
    const d = details.value;
    if (!live || !d) return null;
    return {
      title: live.title ?? null,
      artist: live.artist ?? null,
      album: live.album ?? null,
      artwork: live.artwork ?? null,
      trackUri: d.track_uri,
      albumUri: d.album_uri,
      artists: d.artists,
      contextUri: d.context_uri,
      contextName: d.context_name,
    };
  });

  // The play order around the track, as go-librespot holds it (the user's
  // queue and shuffling included, up to 32 tracks on either side): what plays
  // and what follows. A track with nothing around it (one played on its own)
  // is still what plays.
  const queue = computed(() => details.value?.queue ?? []);
  const queueIndex = computed(() => details.value?.queue_index ?? null);
  const upNext = computed(() => {
    if (queueIndex.value != null) return queue.value.slice(queueIndex.value);
    const live = session.value;
    if (!live || !details.value?.track_uri) return [];
    return [{
      uri: details.value.track_uri, title: live.title ?? null, artist: live.artist ?? null,
      thumbnail: live.artwork ?? null, duration_ms: live.duration_ms ?? null,
    }];
  });

  // What another device of the account plays while nothing plays here, as the
  // record the players draw (utils/nowPlayingMetadata): the bar shows it, and
  // its play button (`take_over`) brings it here.
  const remote = computed(() => remoteRecordOf(details.value));

  // =========================================================================
  // HOME — Spotify's home for the signed-in account, then its playlists
  // =========================================================================
  const home = ref(null);
  const homeLoading = ref(false);
  // 'not_signed_in' | 'unavailable' | null
  const homeError = ref(null);
  // Moved by a change of account: a home asked before it is the last one's.
  let homeGeneration = 0;

  async function loadHome({ force = false } = {}) {
    if (homeLoading.value || (home.value && !force)) return;
    const generation = homeGeneration;
    homeLoading.value = true;
    const result = await apiCall.get(`${BASE}/home`, {
      // Spotify titles its shelves in the language asked for.
      params: { locale: bcp47For(i18n.currentLanguage.value) },
      category: 'spotify',
      message: 'Error loading the Spotify library',
      logLevel: 'warn',
    });
    if (generation !== homeGeneration) return;
    homeLoading.value = false;
    if (!result.ok) {
      homeError.value = result.error?.status === NOT_SIGNED_IN ? 'not_signed_in' : 'unavailable';
      return;
    }
    homeError.value = null;
    home.value = result.data;
  }

  // =========================================================================
  // CONTEXTS — a playlist's, album's, artist's or Liked Songs' tracks
  // =========================================================================
  // uri → { complete, cached, length, tracks } (tracks: as far as described)
  const contexts = ref({});
  // uri → 'not_signed_in' | 'unavailable'
  const contextErrors = ref({});

  /**
   * Load a context's listing, its tracks added as go-librespot describes them
   * (front to back, the first 100 within a second). The route answers as soon
   * as there are tracks past the ones sent along as `after`: asking again until
   * `complete` is the whole protocol, so there is no timer here. A listing left
   * half loaded (the page closed) carries on from where it stopped.
   */
  async function loadContext(uri, { signal } = {}) {
    if (contexts.value[uri]?.complete) return;
    const { [uri]: _dropped, ...otherErrors } = contextErrors.value;
    contextErrors.value = otherErrors;
    for (let idle = 0; idle < MAX_IDLE_ROUNDS;) {
      const entry = contexts.value[uri];
      const known = entry?.tracks ?? [];
      const result = await apiCall.get(`${BASE}/contexts/${encodeURIComponent(uri)}`, {
        params: { after: known.length },
        category: 'spotify',
        message: 'Error loading a Spotify list',
        logLevel: 'warn',
        signal,
      });
      if (signal?.aborted) return;
      if (!result.ok) {
        contextErrors.value = {
          ...contextErrors.value,
          [uri]: result.error?.status === NOT_SIGNED_IN ? 'not_signed_in' : 'unavailable',
        };
        return;
      }
      // Another load of this listing wrote meanwhile: ask again from what it has.
      if (contexts.value[uri] !== entry) continue;
      const { complete, cached, length, tracks } = result.data;
      contexts.value = { ...contexts.value, [uri]: { complete, cached, length, tracks: known.concat(tracks) } };
      if (complete) return;
      idle = tracks.length ? 0 : idle + 1;
    }
    contextErrors.value = { ...contextErrors.value, [uri]: 'unavailable' };
  }

  // =========================================================================
  // ARTISTS — an artist's page as Spotify's apps draw it, but its popular tracks
  // =========================================================================
  // uri → { name, image, monthly_listeners, popular_title, sections }
  const artists = ref({});
  // uri → 'not_signed_in' | 'unavailable'
  const artistErrors = ref({});
  let artistsLoading = new Set();
  // Moved by a change of account or language: an answer asked before it is
  // the last one's, and dropped.
  let artistsGeneration = 0;

  async function loadArtist(uri, { force = false } = {}) {
    if (artistsLoading.has(uri) || (artists.value[uri] && !force)) return;
    const generation = artistsGeneration;
    artistsLoading.add(uri);
    const result = await apiCall.get(`${BASE}/artists/${encodeURIComponent(uri)}`, {
      // Spotify titles the page's sections in the language asked for.
      params: { locale: bcp47For(i18n.currentLanguage.value) },
      category: 'spotify',
      message: 'Error loading a Spotify artist',
      logLevel: 'warn',
    });
    if (generation !== artistsGeneration) return;
    artistsLoading.delete(uri);
    if (!result.ok) {
      artistErrors.value = {
        ...artistErrors.value,
        [uri]: result.error?.status === NOT_SIGNED_IN ? 'not_signed_in' : 'unavailable',
      };
      return;
    }
    const { [uri]: _dropped, ...otherErrors } = artistErrors.value;
    artistErrors.value = otherErrors;
    artists.value = { ...artists.value, [uri]: result.data };
  }

  // Every artist page asked so far is forgotten; the one on screen asks again.
  function forgetArtists() {
    artistsGeneration += 1;
    artistsLoading = new Set();
    artists.value = {};
    artistErrors.value = {};
  }

  // =========================================================================
  // TRACK MENU — what a row's ⋯ menu needs to know before it opens
  // =========================================================================
  // Each asked once per uri and account, the request itself kept so a second
  // press waits on the first; a failure is forgotten, to be asked again.
  let radios = new Map();
  let contextLengths = new Map();

  function askOnce(cache, uri, ask) {
    if (!cache.has(uri)) {
      const asked = ask().then((answer) => {
        if (answer === null) cache.delete(uri);
        return answer;
      });
      cache.set(uri, asked);
    }
    return cache.get(uri);
  }

  // The playlist Spotify makes as a track's radio; null when it gave none.
  function trackRadio(uri) {
    return askOnce(radios, uri, async () => {
      const result = await apiCall.get(`${BASE}/tracks/${encodeURIComponent(uri)}/radio`, {
        category: 'spotify',
        message: 'No Spotify radio for a track',
        logLevel: 'warn',
      });
      return result.ok ? result.data.uri : null;
    });
  }

  // How many tracks a context holds — the first answer of its listing says,
  // well before the listing is complete (an album: ~0.5 s, measured).
  function contextLength(uri) {
    return askOnce(contextLengths, uri, async () => {
      const result = await apiCall.get(`${BASE}/contexts/${encodeURIComponent(uri)}`, {
        category: 'spotify',
        message: 'Error reading the length of a Spotify list',
        logLevel: 'warn',
      });
      return result.ok ? result.data.length : null;
    });
  }

  // =========================================================================
  // PLAYLISTS — what a row's ⋯ menu adds to the account's playlists, and
  // removes from the one on screen
  // =========================================================================
  // The track the "Add to playlist" picker is open for; null while closed.
  const pickerTrack = ref(null);

  function requestAddToPlaylist(track) {
    pickerTrack.value = track;
  }

  function closePlaylistPicker() {
    pickerTrack.value = null;
  }

  // Whether the account may edit a list: its Liked Songs, or a playlist its
  // home lists as its own (owned, or one it may edit).
  function canEdit(uri) {
    const library = home.value;
    if (!library) return false;
    return uri === library.liked_songs_uri || (library.playlists?.mine ?? []).some((p) => p.uri === uri);
  }

  // The picker's rows: the playlists the account may edit, each saying
  // whether it holds the track already, and whether Liked Songs does (null
  // for an episode). Read fresh at each opening.
  async function trackPlaylists(uri) {
    const result = await apiCall.get(`${BASE}/tracks/${encodeURIComponent(uri)}/playlists`, {
      category: 'spotify',
      message: 'Error loading the playlists a Spotify track can be added to',
    });
    return result.ok ? result.data : null;
  }

  // go-librespot lists a list again after a write: the listing kept here is
  // read again from the top, by the page that shows it or the next to open it.
  function relist(uri) {
    contextLengths.delete(uri);
    if (!contexts.value[uri]) return;
    const { [uri]: _dropped, ...rest } = contexts.value;
    contexts.value = rest;
    loadContext(uri);
  }

  // The row removed leaves the listing at once; the entries after it move up
  // one place, as they now are in the playlist.
  function dropRow(uri, position) {
    const entry = contexts.value[uri];
    if (!entry) return;
    const tracks = entry.tracks
      .filter((track) => track.position !== position)
      .map((track) => (track.position > position ? { ...track, position: track.position - 1 } : track));
    contexts.value = {
      ...contexts.value,
      [uri]: { ...entry, tracks, length: Math.max(0, entry.length - 1), cached: Math.max(0, entry.cached - 1) },
    };
  }

  // Added as the list it came from names it, which is what the picker asks
  // the playlists about.
  async function addToPlaylist(playlistUri, track) {
    const result = await apiCall.post(`${BASE}/contexts/${encodeURIComponent(playlistUri)}/tracks`, {
      uris: [track.listed_uri ?? track.uri],
    }, {
      category: 'spotify',
      message: 'Error adding a track to a Spotify playlist',
    });
    if (result.ok) relist(playlistUri);
    return result.ok;
  }

  /**
   * Remove a track from a playlist or Liked Songs. From its row (`row`), the
   * entry that row is — a playlist may hold a track twice; from the picker,
   * the first copy. Either way the item is named as the playlist lists it,
   * which a relinked track's own uri is not. A row moved meanwhile (409:
   * edited on another device) removes nothing, and the listing is read again.
   */
  async function removeFromPlaylist(playlistUri, track, { row = false } = {}) {
    const item = track.listed_uri ?? track.uri;
    const result = await apiCall.delete(
      `${BASE}/contexts/${encodeURIComponent(playlistUri)}/tracks/${encodeURIComponent(item)}`, {
        params: row ? { position: track.position } : undefined,
        category: 'spotify',
        message: 'Error removing a track from a Spotify playlist',
      });
    if (result.ok && row) dropRow(playlistUri, track.position);
    else if (result.ok || result.error?.status === 409) relist(playlistUri);
    return result.ok;
  }

  // =========================================================================
  // PROFILES — the accounts that cast to Milō
  // =========================================================================
  // Pushed whole (`source/profiles_changed`) when one is kept, described or
  // forgotten: a new account is named in the audio state before its profile
  // exists, and its picture comes later still. The one signed in is the
  // state's account, so a switch needs no list read again.
  const kept = ref([]);
  const profiles = computed(() =>
    kept.value.map((profile) => ({ ...profile, active: profile.username === account.value }))
  );
  // Moved by every push: a list read before it is older than the push.
  let profilePushes = 0;

  async function loadProfiles() {
    const pushes = profilePushes;
    const result = await apiCall.get(`${BASE}/profiles`, {
      category: 'spotify',
      message: 'Error loading Spotify profiles',
      logLevel: 'warn',
    });
    if (result.ok && pushes === profilePushes) kept.value = result.data.profiles;
    return result.ok;
  }

  // =========================================================================
  // HISTORY — what the daemon's player played, newest first, each track with
  // the context it played in (not the play order's previous tracks, which
  // stop at the start of the context)
  // =========================================================================
  const history = ref([]);
  const historyError = ref(false);
  // Bumped by each read and by an account switch: only the latest read for
  // the account shown lands.
  let historyReads = 0;

  async function loadHistory() {
    const read = ++historyReads;
    const result = await apiCall.get(`${BASE}/history`, {
      category: 'spotify',
      message: 'Error loading the Spotify history',
      logLevel: 'warn',
    });
    if (read !== historyReads) return;
    historyError.value = !result.ok;
    if (result.ok) history.value = result.data.tracks;
  }

  /** WS: source/profiles_changed — a profile kept, described or forgotten. */
  function applyProfiles(event) {
    profilePushes += 1;
    kept.value = event.data.profiles;
  }

  // App.vue's reconnect / tab-visible: a push missed meanwhile is gone.
  async function resync() {
    return loadProfiles();
  }

  async function switchProfile(username) {
    const result = await apiCall.put(`${BASE}/active-profile`, { username }, {
      category: 'spotify',
      message: 'Error switching Spotify profile',
    });
    return result.ok;
  }

  async function forgetProfile(username) {
    const result = await apiCall.delete(`${BASE}/profiles/${encodeURIComponent(username)}`, {
      category: 'spotify',
      message: 'Error forgetting a Spotify profile',
    });
    return result.ok;
  }

  // Another account's library: nothing loaded for the last one still holds.
  // Synchronous, so the home view asking for the next account's library always
  // finds it cleared, never cleared after it asked.
  watch(account, (now, before) => {
    if (now === before) return;
    homeGeneration += 1;
    homeLoading.value = false;
    home.value = null;
    homeError.value = null;
    contexts.value = {};
    contextErrors.value = {};
    forgetArtists();
    radios = new Map();
    contextLengths = new Map();
    pickerTrack.value = null;
    historyReads += 1;
    history.value = [];
    historyError.value = false;
  }, { flush: 'sync' });

  // Another interface language: the shelves and the artist pages are titled
  // in the last one.
  watch(i18n.currentLanguage, () => {
    if (home.value) loadHome({ force: true });
    forgetArtists();
  });

  // =========================================================================
  // COMMANDS
  // =========================================================================
  const send = (command, data) => unifiedStore.sendCommand('spotify', command, data);

  function playContext(uri, { skipToUri = null, shuffle: shuffled = false } = {}) {
    const data = { uri, shuffle: shuffled };
    if (skipToUri) data.skip_to_uri = skipToUri;
    return send('play_context', data);
  }

  // A track of the history, in the context it played in: it is not in the
  // play order any more. One played on its own plays on its own again.
  function playFromHistory(track) {
    if (!track.context_uri) return playContext(track.uri);
    return playContext(track.context_uri, { skipToUri: track.uri });
  }

  return {
    // now playing
    account, signingIn, session, phase, isPlaying,
    currentTrackUri, currentContextUri, nowPlaying, remote,
    // play order
    upNext,
    // history
    history, historyError, loadHistory,
    // home
    home, homeLoading, homeError, loadHome,
    // contexts
    contexts, contextErrors, loadContext,
    // artists
    artists, artistErrors, loadArtist,
    // track menu
    trackRadio, contextLength,
    // playlists
    pickerTrack, requestAddToPlaylist, closePlaylistPicker, canEdit, trackPlaylists,
    addToPlaylist, removeFromPlaylist,
    // profiles
    profiles, loadProfiles, applyProfiles, switchProfile, forgetProfile,
    resync,
    // commands
    playContext, playFromHistory,
  };
});
