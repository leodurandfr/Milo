// frontend/tests/composables/playlistPickerRemount.test.js
/**
 * A playlist picker born open reads what it shows. Both pickers (Spotify,
 * Music Library) are driven by a store that outlives the source view, and the
 * view is unmounted and mounted again by any switch, outage or multiroom
 * toggle while a picker is open. If this fails, the Spotify picker comes back
 * on skeleton rows forever, with no retry, and the Music Library one shows
 * every playlist unchecked — a tap on one already holding the songs adds them
 * a second time.
 *
 * Each picker is mounted as its source mounts it, its store already holding
 * the open request; what is asserted is what it asked the backend (through the
 * apiCall boundary), nothing about its DOM.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { apiCall } from '@/services/apiCall';
import { resetApiCallMock } from '../helpers/apiCallMock';
import { useSpotifyStore } from '@/stores/spotifyStore';
import { useMusicLibraryStore } from '@/stores/musicLibraryStore';
import SpotifyAddToPlaylistModal from '@/components/spotify/SpotifyAddToPlaylistModal.vue';
import AddToPlaylistModal from '@/components/music-library/AddToPlaylistModal.vue';

vi.mock('@/services/apiCall', () => import('../helpers/apiCallMock'));

const getUrls = () => apiCall.get.mock.calls.map(([url]) => url);

describe('playlist pickers mounted already open', () => {
  const wrappers = [];
  const host = (component, props = {}) => {
    const wrapper = mount(component, {
      props,
      attachTo: document.body,
      global: { directives: { press: {} } },
    });
    wrappers.push(wrapper);
    return wrapper;
  };

  beforeEach(() => resetApiCallMock());
  afterEach(() => wrappers.splice(0).forEach((w) => w.unmount()));

  it("Spotify's reads which playlists hold its track", async () => {
    const uri = 'spotify:track:says';
    useSpotifyStore().requestAddToPlaylist({ uri, name: 'Says' });

    host(SpotifyAddToPlaylistModal);
    await flushPromises();

    expect(getUrls()).toContain(`/api/spotify/tracks/${encodeURIComponent(uri)}/playlists`);
  });

  it("Music Library's reads which playlists hold its songs", async () => {
    const store = useMusicLibraryStore();
    store.requestAddToPlaylist(['song-1']);

    host(AddToPlaylistModal, { isOpen: true, songIds: store.addToPlaylistSongIds });
    await flushPromises();

    expect(getUrls().some((url) => url.endsWith('/playlists/containing'))).toBe(true);
  });
});
