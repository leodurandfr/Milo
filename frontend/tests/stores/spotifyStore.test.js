// frontend/tests/stores/spotifyStore.test.js
/**
 * spotifyStore is the browser's memory of one account's library. What can
 * break, silently: a list cached for the last account shown under the next one
 * (a guest's cast replaces the signed-in account mid-browse), a heart that
 * stays turned after Spotify refused it, a listing that stops at its first
 * "not ready yet" answer and shows an empty playlist, and the profile screen
 * opening over music someone is listening to.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { nextTick } from 'vue';
import { setActivePinia, createPinia } from 'pinia';
import { useSpotifyStore } from '@/stores/spotifyStore';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { apiCall } from '@/services/apiCall';
import { resetApiCallMock, ok, fail } from '../helpers/apiCallMock';
import { publishState, makeSession } from '../helpers/audioState';

vi.mock('@/services/apiCall', () => import('../helpers/apiCallMock'));

const PLAYLIST = 'spotify:playlist:chill';
const TRACK = 'spotify:track:says';

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
    apiCall.get.mockResolvedValueOnce(ok({ status: 'success', account: 'owner', liked_songs_uri: 'x', sections: {} }));
    await store.loadHome();
    store.liked[TRACK] = true;
    expect(store.home).not.toBeNull();

    publish({ details: details({ account: 'guest' }) });
    await nextTick();

    expect(store.home).toBeNull();
    expect(store.contexts).toEqual({});
    expect(store.isLiked(TRACK)).toBeNull();
  });

  it('turns a heart back when Spotify refuses it', async () => {
    apiCall.put.mockResolvedValueOnce(fail('refused', 503));

    const accepted = await store.setLiked(TRACK, true);

    expect(accepted).toBe(false);
    expect(store.isLiked(TRACK)).toBeNull();
  });

  it('asks for a listing again until go-librespot has all of it', async () => {
    apiCall.get
      .mockResolvedValueOnce(ok({ ready: false, cached: 120, length: 618 }))
      .mockResolvedValueOnce(ok({ ready: false, cached: 400, length: 618 }))
      .mockResolvedValueOnce(ok({ ready: true, cached: 618, length: 618, tracks: [{ uri: TRACK }] }));

    await store.loadContext(PLAYLIST);

    expect(apiCall.get.mock.calls.filter(([url]) => url.includes('/contexts/'))).toHaveLength(3);
    expect(store.contexts[PLAYLIST]).toMatchObject({ ready: true, tracks: [{ uri: TRACK }] });
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

  it('opens on the profile screen only with several profiles and nothing playing', () => {
    store.profiles = [{ username: 'owner' }];
    publish({ details: details() });
    expect(store.opensOnProfiles).toBe(false);

    store.profiles = [{ username: 'owner' }, { username: 'guest' }];
    expect(store.opensOnProfiles).toBe(true);

    publish({ session: makeSession(), details: details({ track_uri: TRACK }) });
    expect(store.opensOnProfiles).toBe(false);
  });

  it('reads whether the playing track is liked, and cycles repeat from where it is', async () => {
    publish({ session: makeSession(), details: details({ track_uri: TRACK, repeat: 'context' }) });
    await nextTick();

    const likedRead = apiCall.get.mock.calls.find(([url]) => url === '/api/spotify/liked-tracks');
    expect(likedRead?.[1]?.params).toEqual({ uris: TRACK });

    await store.cycleRepeat();
    expect(commandsSent()).toEqual([{ command: 'set_repeat', data: { mode: 'track' } }]);
  });
});
