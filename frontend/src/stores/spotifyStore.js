import { defineStore } from 'pinia';
import { ref, computed, watch } from 'vue';
import { apiCall } from '@/services/apiCall';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { i18n } from '@/services/i18n';
import { bcp47For } from '@/constants/countries';

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
      artistUri: d.artist_uri,
      contextUri: d.context_uri,
      contextName: d.context_name,
    };
  });

  // =========================================================================
  // HOME — Spotify's home for the signed-in account, then its playlists
  // =========================================================================
  const home = ref(null);
  const homeLoading = ref(false);
  // 'not_signed_in' | 'unavailable' | null
  const homeError = ref(null);

  async function loadHome({ force = false } = {}) {
    if (homeLoading.value || (home.value && !force)) return;
    homeLoading.value = true;
    const result = await apiCall.get(`${BASE}/home`, {
      // Spotify titles its shelves in the language asked for.
      params: { locale: bcp47For(i18n.currentLanguage.value) },
      category: 'spotify',
      message: 'Error loading the Spotify library',
      logLevel: 'warn',
    });
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
  // PROFILES — the accounts that cast to Milō
  // =========================================================================
  const profiles = ref([]);
  const profilesLoaded = ref(false);

  async function loadProfiles() {
    const result = await apiCall.get(`${BASE}/profiles`, {
      category: 'spotify',
      message: 'Error loading Spotify profiles',
      logLevel: 'warn',
    });
    if (!result.ok) return;
    profiles.value = result.data.profiles;
    profilesLoaded.value = true;
  }

  async function switchProfile(username) {
    const result = await apiCall.put(`${BASE}/active-profile`, { username }, {
      category: 'spotify',
      message: 'Error switching Spotify profile',
    });
    return result.ok;
  }

  async function renameProfile(username, name) {
    const result = await apiCall.patch(`${BASE}/profiles/${encodeURIComponent(username)}`, { name }, {
      category: 'spotify',
      message: 'Error renaming a Spotify profile',
    });
    if (result.ok) await loadProfiles();
    return result.ok;
  }

  async function forgetProfile(username) {
    const result = await apiCall.delete(`${BASE}/profiles/${encodeURIComponent(username)}`, {
      category: 'spotify',
      message: 'Error forgetting a Spotify profile',
    });
    if (result.ok) await loadProfiles();
    return result.ok;
  }

  // With several profiles and nothing playing, the browser opens on the
  // profile screen: whoever picks Spotify picks whose library it is.
  const opensOnProfiles = computed(() => profiles.value.length >= 2 && !session.value);

  // Another account's library: nothing loaded for the last one still holds.
  watch(account, (now, before) => {
    if (now === before) return;
    home.value = null;
    homeError.value = null;
    contexts.value = {};
    contextErrors.value = {};
    radios = new Map();
    contextLengths = new Map();
    if (now) loadProfiles();
  });

  // Another interface language: the shelves are titled in the last one.
  watch(i18n.currentLanguage, () => {
    if (home.value) loadHome({ force: true });
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

  return {
    // now playing
    account, signingIn, session, phase, isPlaying,
    currentTrackUri, currentContextUri, nowPlaying,
    // home
    home, homeLoading, homeError, loadHome,
    // contexts
    contexts, contextErrors, loadContext,
    // track menu
    trackRadio, contextLength,
    // profiles
    profiles, profilesLoaded, opensOnProfiles, loadProfiles, switchProfile, renameProfile, forgetProfile,
    // commands
    playContext,
  };
});
