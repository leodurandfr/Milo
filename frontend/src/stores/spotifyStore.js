import { defineStore } from 'pinia';
import { ref, computed, watch } from 'vue';
import { apiCall } from '@/services/apiCall';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { i18n } from '@/services/i18n';
import { bcp47For } from '@/constants/countries';

const BASE = '/api/spotify';
// go-librespot's /library/liked answers for 1 to 50 tracks per call.
const LIKED_BATCH = 50;
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
 * The Spotify browser: what the signed-in account's library holds, which of
 * its tracks are liked, and the profiles Milō keeps. What plays — and whose
 * library it is — is derived from the audio state (`details.spotify`), never
 * fetched.
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
      if (uri === home.value?.liked_songs_uri) markLiked(tracks);
      if (complete) return;
      idle = tracks.length ? 0 : idle + 1;
    }
    contextErrors.value = { ...contextErrors.value, [uri]: 'unavailable' };
  }

  // =========================================================================
  // LIKED — which tracks are in Liked Songs
  // =========================================================================
  // uri → boolean; absent: not known yet (TrackRow draws no heart)
  const liked = ref({});

  const isLiked = (uri) => (uri in liked.value ? liked.value[uri] : null);

  function markLiked(tracks) {
    const next = { ...liked.value };
    for (const track of tracks) next[track.uri] = true;
    liked.value = next;
  }

  async function fetchLiked(uris) {
    const unknown = [...new Set(uris)].filter((uri) => uri && !(uri in liked.value));
    for (let start = 0; start < unknown.length; start += LIKED_BATCH) {
      const batch = unknown.slice(start, start + LIKED_BATCH);
      const result = await apiCall.get(`${BASE}/liked-tracks`, {
        params: { uris: batch.join(',') },
        category: 'spotify',
        message: 'Error reading Spotify liked tracks',
        logLevel: 'warn',
      });
      if (!result.ok) return;
      const next = { ...liked.value };
      for (const item of result.data.items) next[item.uri] = item.liked;
      liked.value = next;
    }
  }

  // Optimistic: the heart turns at once and turns back if Spotify refused.
  async function setLiked(uri, on) {
    if (!uri) return false;
    const before = isLiked(uri);
    liked.value = { ...liked.value, [uri]: on };
    const path = `${BASE}/liked-tracks/${encodeURIComponent(uri)}`;
    const options = { category: 'spotify', message: `Error ${on ? 'liking' : 'unliking'} a Spotify track` };
    const result = on ? await apiCall.put(path, {}, options) : await apiCall.delete(path, options);
    if (!result.ok) {
      const reverted = { ...liked.value };
      if (before === null) delete reverted[uri];
      else reverted[uri] = before;
      liked.value = reverted;
      return false;
    }
    return true;
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
    liked.value = {};
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
    // liked
    liked, isLiked, fetchLiked, setLiked,
    // profiles
    profiles, profilesLoaded, opensOnProfiles, loadProfiles, switchProfile, renameProfile, forgetProfile,
    // commands
    playContext,
  };
});
