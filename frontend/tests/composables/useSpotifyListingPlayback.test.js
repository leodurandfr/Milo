// frontend/tests/composables/useSpotifyListingPlayback.test.js
/**
 * useSpotifyListingPlayback decides which row of a Spotify list (a playlist,
 * Liked Songs, an album, an artist's popular tracks) is playing, and plays a
 * row. What breaks: the playing row left dark, or lit in every list that holds
 * the track, and a tap asking for a uri the context does not hold, which
 * go-librespot answers by playing the list's first track.
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
const LIKE_THIS = 'spotify:track:3z017xyjPKW4o2WoDgswge';
// The row as /api/spotify/contexts serves it.
const ROW = { uri: LIKE_THIS, position: 0, title: 'Like This', artists: [] };

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

  it('lights the row playing from this list', () => {
    const listing = mountListing(LIKED);
    publish(LIKED, LIKE_THIS);
    expect(listing.isCurrent(ROW)).toBe(true);
  });

  it('lights no row for the same entry played from another list', () => {
    const listing = mountListing(LIKED);
    publish('spotify:album:6JShZuyv8WbXkKLehzuvm0', LIKE_THIS);
    expect(listing.isCurrent(ROW)).toBe(false);
  });

  it('plays the list from the row tapped', async () => {
    const listing = mountListing(LIKED);
    await listing.play(ROW);
    expect(playsSent()).toEqual([
      { command: 'play_context', data: { uri: LIKED, skip_to_uri: LIKE_THIS } },
    ]);
  });
});
