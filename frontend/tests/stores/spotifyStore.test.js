// frontend/tests/stores/spotifyStore.test.js
/**
 * spotifyStore is the browser's memory of one account's library. What can
 * break, silently: a list cached for the last account shown under the next one
 * (a guest's cast replaces the signed-in account mid-browse), a listing that stops at its first
 * partial answer and shows a playlist cut short (or one that asks for its
 * tracks from the start again and draws them twice), and the profile screen
 * opening over music someone is listening to, and Spotify's shelves left titled
 * in the language the interface just left.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { nextTick } from 'vue';
import { setActivePinia, createPinia } from 'pinia';
import { useSpotifyStore } from '@/stores/spotifyStore';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { apiCall } from '@/services/apiCall';
import { i18n } from '@/services/i18n';
import { resetApiCallMock, ok, fail } from '../helpers/apiCallMock';
import { publishState, makeSession } from '../helpers/audioState';

vi.mock('@/services/apiCall', () => import('../helpers/apiCallMock'));

const PLAYLIST = 'spotify:playlist:chill';
const TRACK = 'spotify:track:says';
const HOME = {
  status: 'success', account: 'owner', liked_songs_uri: 'x', shortcuts: [], shelves: [],
  playlists: { mine: [], followed: [] },
};
const homeLocales = () =>
  apiCall.get.mock.calls.filter(([url]) => url === '/api/spotify/home').map(([, options]) => options.params.locale);

function details(overrides = {}) {
  return {
    kind: 'spotify', account: 'owner', signing_in: false,
    context_uri: null, context_name: null, track_uri: null, album_uri: null, artist_uri: null,
    shuffle: false, repeat: 'off', ...overrides,
  };
}

const commandsSent = () =>
  apiCall.post.mock.calls
    .filter(([url]) => url === '/api/audio/control/spotify')
    .map(([, body]) => body);

describe('spotifyStore', () => {
  let store;
  let unified;

  const publish = (overrides) => publishState(unified, { source: 'spotify', service: 'running', ...overrides });

  beforeEach(() => {
    setActivePinia(createPinia());
    resetApiCallMock();
    apiCall.get.mockResolvedValue(ok({ status: 'success', items: [], profiles: [] }));
    apiCall.post.mockResolvedValue(ok({ status: 'success' }));
    unified = useUnifiedAudioStore();
    store = useSpotifyStore();
  });

  // A store outlives its test's pinia: its watchers would answer the next
  // test's language change.
  afterEach(() => store.$dispose());

  it('describes the playing track from the session and the details', () => {
    publish({
      session: makeSession({ title: 'Says', artist: 'Nils Frahm' }),
      details: details({ context_uri: PLAYLIST, track_uri: TRACK, album_uri: 'spotify:album:spaces', artist_uri: 'spotify:artist:nils' }),
    });

    expect(store.nowPlaying).toMatchObject({
      title: 'Says', artist: 'Nils Frahm', trackUri: TRACK, contextUri: PLAYLIST,
      albumUri: 'spotify:album:spaces', artistUri: 'spotify:artist:nils',
    });
  });

  it('drops what it loaded for one account when another signs in', async () => {
    publish({ details: details() });
    apiCall.get.mockResolvedValueOnce(ok(HOME));
    await store.loadHome();
    expect(store.home).not.toBeNull();

    publish({ details: details({ account: 'guest' }) });
    await nextTick();

    expect(store.home).toBeNull();
    expect(store.contexts).toEqual({});
  });

  it("drops the last account's home still in flight, and loads the next account's", async () => {
    publish({ details: details() });
    let answerOwner;
    apiCall.get.mockReturnValueOnce(new Promise((resolve) => { answerOwner = resolve; }));
    const ownerHome = store.loadHome();

    publish({ details: details({ account: 'guest' }) });
    apiCall.get.mockResolvedValueOnce(ok({ ...HOME, account: 'guest' }));
    await store.loadHome();
    answerOwner(ok(HOME));
    await ownerHome;

    expect(homeLocales()).toHaveLength(2);
    expect(store.home.account).toBe('guest');
    expect(store.homeLoading).toBe(false);
  });

  it("marks the state's account as the active profile, with no list read at a switch", () => {
    const owner = { username: 'owner', name: 'Léo', avatar_url: null };
    const guest = { username: 'guest', name: 'Cla', avatar_url: null };
    store.applyProfiles({ data: { source: 'spotify', profiles: [owner, guest] } });
    publish({ details: details({ account: 'guest' }) });

    expect(store.profiles.filter((p) => p.active).map((p) => p.username)).toEqual(['guest']);
    expect(apiCall.get.mock.calls.filter(([url]) => url === '/api/spotify/profiles')).toEqual([]);
  });

  it('keeps a pushed list over a read that answers after it', async () => {
    let answerRead;
    apiCall.get.mockReturnValueOnce(new Promise((resolve) => { answerRead = resolve; }));
    const read = store.resync();

    const guest = { username: 'guest', name: 'Cla', avatar_url: 'https://i.scdn.co/image/cla' };
    store.applyProfiles({ data: { source: 'spotify', profiles: [guest] } });
    answerRead(ok({ status: 'success', profiles: [] }));

    expect(await read).toBe(true);
    expect(store.profiles.map((p) => p.username)).toEqual(['guest']);
  });

  it("asks for Spotify's home in the interface language, and again when it changes", async () => {
    i18n.currentLanguage.value = 'french';
    apiCall.get.mockResolvedValue(ok(HOME));
    await store.loadHome();

    i18n.currentLanguage.value = 'portuguese';
    await nextTick();
    await vi.waitFor(() => expect(homeLocales()).toHaveLength(2));

    expect(homeLocales()).toEqual(['fr', 'pt-PT']);
    i18n.currentLanguage.value = 'english';
  });

  it("asks a track's radio once for two presses, and again after a failure", async () => {
    // Every ⋯ press asks before its menu opens: a second press must wait on the
    // first request, and a failed one must not leave the radio out for good.
    const RADIO = 'spotify:playlist:radio';
    apiCall.get.mockResolvedValueOnce(fail('unavailable', 503));
    expect(await store.trackRadio(TRACK)).toBeNull();

    apiCall.get.mockResolvedValueOnce(ok({ status: 'success', uri: RADIO }));
    const [first, second] = await Promise.all([store.trackRadio(TRACK), store.trackRadio(TRACK)]);

    expect([first, second]).toEqual([RADIO, RADIO]);
    expect(apiCall.get).toHaveBeenCalledTimes(2);
  });

  it('adds a listing\'s tracks as they are described, asking from what it has', async () => {
    // The route as measured: go-librespot describes a listing front to back,
    // and each answer carries the tracks past `after`.
    const listing = ['a', 'b', 'c', 'd', 'e'].map((n) => ({ uri: `spotify:track:${n}` }));
    let described = 0;
    apiCall.get.mockImplementation(async (url, { params }) => {
      described = Math.min(described + 2, listing.length);
      return ok({
        complete: described === listing.length, cached: described, length: listing.length,
        tracks: listing.slice(params.after, described),
      });
    });

    await store.loadContext(PLAYLIST);

    expect(apiCall.get.mock.calls.map(([, options]) => options.params.after)).toEqual([0, 2, 4]);
    expect(store.contexts[PLAYLIST]).toMatchObject({ complete: true, tracks: listing });
  });

  it('gives a listing up once it stops moving, keeping what it has', async () => {
    apiCall.get
      .mockResolvedValueOnce(ok({ complete: false, cached: 1, length: 9, tracks: [{ uri: TRACK }] }))
      .mockResolvedValue(ok({ complete: false, cached: 1, length: 9, tracks: [] }));

    await store.loadContext(PLAYLIST);

    expect(store.contextErrors[PLAYLIST]).toBe('unavailable');
    expect(store.contexts[PLAYLIST].tracks).toEqual([{ uri: TRACK }]);
  });

  it('stops asking once the page that wanted the listing is gone', async () => {
    const controller = new AbortController();
    apiCall.get.mockImplementation(async () => {
      controller.abort();
      return { ok: false, data: null, error: null };
    });

    await store.loadContext(PLAYLIST, { signal: controller.signal });

    expect(apiCall.get).toHaveBeenCalledTimes(1);
    expect(store.contextErrors[PLAYLIST]).toBeUndefined();
  });

  it('plays a context from a track, shuffled or not, as one command', async () => {
    await store.playContext(PLAYLIST, { skipToUri: TRACK });
    await store.playContext(PLAYLIST, { shuffle: true });

    expect(commandsSent()).toEqual([
      { command: 'play_context', data: { uri: PLAYLIST, shuffle: false, skip_to_uri: TRACK } },
      { command: 'play_context', data: { uri: PLAYLIST, shuffle: true } },
    ]);
  });
});
