// frontend/tests/composables/musicLibraryPlaylistReread.test.js
/**
 * A Music Library playlist page holds its own copy of the playlist's entries
 * and sends every edit with the order it was made from. When the "add to
 * playlist" picker writes that playlist — from one of the page's own rows, or
 * from a page in front of it — the page must read the playlist again: a copy
 * left as it was keeps a row the picker removed, and the next reorder or
 * removal is refused (409, with its banner) and undone on screen.
 *
 * The page is mounted with its children stubbed; what is asserted is the read
 * it sends (through the apiCall boundary), nothing about its DOM.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { shallowMount, flushPromises } from '@vue/test-utils';
import { apiCall } from '@/services/apiCall';
import { resetApiCallMock, ok } from '../helpers/apiCallMock';
import { useMusicLibraryStore } from '@/stores/musicLibraryStore';
import PlaylistView from '@/components/music-library/views/PlaylistView.vue';

vi.mock('@/services/apiCall', () => import('../helpers/apiCallMock'));

const ID = 'pl-1';
const reads = (id) => apiCall.get.mock.calls.filter(([url]) => url.endsWith(`/playlist/${id}`)).length;

describe('Music Library playlist page', () => {
  let wrapper;

  beforeEach(() => {
    resetApiCallMock();
    apiCall.get.mockImplementation((url) => Promise.resolve(
      url.endsWith('/playlists')
        ? ok({ playlists: [] })
        : ok({ id: ID, name: 'Road', entry: [{ id: 'a' }, { id: 'b' }] }),
    ));
    apiCall.put.mockResolvedValue(ok({ status: 'success' }));
  });

  afterEach(() => wrapper?.unmount());

  it.each([
    ['removes', (store) => store.removeFromPlaylist(ID, ['a'])],
    ['adds', (store) => store.addToPlaylist(ID, ['c'])],
  ])('reads the playlist again when the picker %s songs in it', async (_, write) => {
    wrapper = shallowMount(PlaylistView, {
      props: { playlistId: ID },
      global: { directives: { press: {} } },
    });
    await flushPromises();
    const before = reads(ID);
    expect(before).toBeGreaterThan(0);

    await write(useMusicLibraryStore());
    await flushPromises();

    expect(reads(ID)).toBe(before + 1);
  });

  it('leaves the page alone when another playlist is written', async () => {
    wrapper = shallowMount(PlaylistView, {
      props: { playlistId: ID },
      global: { directives: { press: {} } },
    });
    await flushPromises();
    const before = reads(ID);

    await useMusicLibraryStore().removeFromPlaylist('pl-other', ['a']);
    await flushPromises();

    expect(reads(ID)).toBe(before);
  });
});
