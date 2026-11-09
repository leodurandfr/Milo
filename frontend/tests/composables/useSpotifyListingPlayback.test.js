// frontend/tests/composables/useSpotifyListingPlayback.test.js
/**
 * useSpotifyListingPlayback decides which row of a Spotify list (a playlist,
 * Liked Songs, an album, an artist's popular tracks) is playing, and plays a
 * row. A row is its entry as the list holds it: Spotify relinks a track to a
 * market alternative when it streams, and from then on go-librespot describes
 * the row by the alternative's uri. What breaks: the playing row left dark
 * (measured on Liked Songs, 2026-11-09), and a tap on a relinked row asking
 * for a uri the context does not hold, which go-librespot answers by playing
 * the list's first track.
 *
 * The real stores are driven through the unified store's `source/state`
 * handler and the command reaches the apiCall mock; a host component is
 * mounted only for the composable's lifecycle.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { defineComponent, h } from 'vue';
import { mount } from '@vue/test-utils';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useSpotifyListingPlayback } from '@/composables/useSpotifyListingPlayback';
import { apiCall } from '@/services/apiCall';
import { resetApiCallMock, ok } from '../helpers/apiCallMock';
import { publishState, makeSession } from '../helpers/audioState';

vi.mock('@/services/apiCall', () => import('../helpers/apiCallMock'));

const LIKED = 'spotify:user:owner:collection';
const LISTED = 'spotify:track:3z017xyjPKW4o2WoDgswge';
const ALTERNATIVE = 'spotify:track:3h2XdwGrmlH3nkODOVm35Z';
// The row as /api/spotify/contexts serves it once the track has streamed.
const ROW = { uri: ALTERNATIVE, listed_uri: LISTED, position: 0, title: 'Like This', artists: [] };

describe('useSpotifyListingPlayback', () => {
  let unified;

  function mountListing(uri) {
    let listing;
    mount(defineComponent({
      setup() {
        listing = useSpotifyListingPlayback(() => uri);
        return () => h('div');
      },
    }));
    return listing;
  }

  const publish = (contextUri, trackUri) => publishState(unified, {
    source: 'spotify', service: 'running',
    session: makeSession({ phase: 'playing' }),
    details: {
      kind: 'spotify', account: 'owner', signing_in: false,
      context_uri: contextUri, context_name: null, track_uri: trackUri, album_uri: null, artists: [],
      shuffle: false, repeat: 'off',
    },
  });

  const playsSent = () =>
    apiCall.post.mock.calls
      .filter(([url]) => url === '/api/audio/control/spotify')
      .map(([, body]) => body);

  beforeEach(() => {
    resetApiCallMock();
    apiCall.post.mockResolvedValue(ok({ status: 'success' }));
    unified = useUnifiedAudioStore();
  });

  it('lights a relinked row by the uri its list holds', () => {
    const listing = mountListing(LIKED);
    publish(LIKED, LISTED);
    expect(listing.isCurrent(ROW)).toBe(true);
  });

  it('lights the row by its own uri when the state could name no other', () => {
    const listing = mountListing(LIKED);
    publish(LIKED, ALTERNATIVE);
    expect(listing.isCurrent(ROW)).toBe(true);
  });

  it('lights no row for the same entry played from another list', () => {
    const listing = mountListing(LIKED);
    publish('spotify:album:6JShZuyv8WbXkKLehzuvm0', LISTED);
    expect(listing.isCurrent(ROW)).toBe(false);
  });

  it('plays a row by the uri its list holds', async () => {
    const listing = mountListing(LIKED);
    await listing.play(ROW);
    expect(playsSent()).toEqual([
      { command: 'play_context', data: { uri: LIKED, skip_to_uri: LISTED } },
    ]);
  });
});
