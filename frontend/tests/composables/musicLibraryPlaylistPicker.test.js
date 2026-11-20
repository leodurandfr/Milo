// frontend/tests/composables/musicLibraryPlaylistPicker.test.js
/**
 * The Music Library "add to playlist" picker decides from its checkmarks
 * whether a tap adds the songs or removes them, so a checkmark that belongs to
 * other songs is a wrong write. Closed and opened again for another track while
 * the first membership read was still out, the picker took that first answer
 * when it landed last: the second track drew checked in a playlist that holds
 * only the first, and a tap removed it from there — a track that is not in it.
 *
 * A write sent for the first track that lands after the reopening is the same
 * wrong checkmark by another path, and is held to the same answer.
 *
 * A Retry after a Navidrome that answered nothing at the opening must read the
 * playlists again too: one that read only the membership drew it over an empty
 * list, and the playlist holding the song had no row at all.
 *
 * The picker is mounted as its source mounts it; what is asserted is the write
 * it sends (through the apiCall boundary), nothing about its DOM.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { apiCall } from '@/services/apiCall';
import { resetApiCallMock, ok, fail } from '../helpers/apiCallMock';
import AddToPlaylistModal from '@/components/music-library/AddToPlaylistModal.vue';
import ListItemButton from '@/components/ui/ListItemButton.vue';
import MessageContent from '@/components/ui/MessageContent.vue';

vi.mock('@/services/apiCall', () => import('../helpers/apiCallMock'));

const PLAYLIST = { id: 'pl-1', name: 'Road' };

describe('Music Library playlist picker', () => {
  let wrapper;
  // The membership reads, answered by the test in the order it chooses.
  let answers;

  beforeEach(() => {
    resetApiCallMock();
    answers = [];
    apiCall.get.mockImplementation((url) => {
      if (url.endsWith('/playlists/containing')) {
        return new Promise((resolve) => answers.push(resolve));
      }
      if (url.endsWith('/playlists')) return Promise.resolve(ok({ playlists: [PLAYLIST] }));
      return Promise.resolve(ok({ songs: [] }));
    });
    apiCall.put.mockResolvedValue(ok({ status: 'success' }));
  });

  afterEach(() => wrapper?.unmount());

  it('drops a membership answer read for an opening that is over', async () => {
    wrapper = mount(AddToPlaylistModal, {
      props: { isOpen: true, songIds: ['first'] },
      attachTo: document.body,
      global: { directives: { press: {} } },
    });
    await flushPromises();
    await wrapper.setProps({ isOpen: false, songIds: [] });
    await wrapper.setProps({ isOpen: true, songIds: ['second'] });
    await flushPromises();
    expect(answers).toHaveLength(2);

    answers[1](ok({ playlist_ids: [] }));  // "second" is in no playlist
    await flushPromises();
    answers[0](ok({ playlist_ids: [PLAYLIST.id] }));  // "first" is in Road, landing last
    await flushPromises();

    const row = wrapper.findAllComponents(ListItemButton).find((c) => c.props('title') === PLAYLIST.name);
    row.vm.$emit('click');
    await flushPromises();

    const [url, body] = apiCall.put.mock.calls.at(-1);
    expect(url).toBe(`/api/music-library/playlist/${PLAYLIST.id}`);
    expect(body).toEqual({ song_ids_to_add: ['second'] });
  });

  it('reads the playlists again on Retry, not only the membership', async () => {
    // Navidrome does not answer at the opening: every read fails.
    apiCall.get.mockImplementation(() => Promise.resolve(fail('Service Unavailable', 503)));
    wrapper = mount(AddToPlaylistModal, {
      props: { isOpen: true, songIds: ['first'] },
      attachTo: document.body,
      global: { directives: { press: {} } },
    });
    await flushPromises();

    // It is back: "first" is in Road.
    apiCall.get.mockImplementation((url) => {
      if (url.endsWith('/playlists/containing')) return Promise.resolve(ok({ playlist_ids: [PLAYLIST.id] }));
      if (url.endsWith('/playlists')) return Promise.resolve(ok({ playlists: [PLAYLIST] }));
      return Promise.resolve(ok({ songs: [] }));
    });
    await wrapper.findComponent(MessageContent).props('ctaClick')();
    await flushPromises();

    const row = wrapper.findAllComponents(ListItemButton).find((c) => c.props('title') === PLAYLIST.name);
    row.vm.$emit('click');
    await flushPromises();

    const [url, body] = apiCall.put.mock.calls.at(-1);
    expect(url).toBe(`/api/music-library/playlist/${PLAYLIST.id}`);
    expect(body).toEqual({ remove_song_ids: ['first'] });
  });

  it('drops a write sent for an opening that is over', async () => {
    wrapper = mount(AddToPlaylistModal, {
      props: { isOpen: true, songIds: ['first'] },
      attachTo: document.body,
      global: { directives: { press: {} } },
    });
    await flushPromises();
    answers[0](ok({ playlist_ids: [] }));
    await flushPromises();

    // "first" is added to Road; the write is still out when the picker closes.
    let landWrite;
    apiCall.put.mockImplementationOnce(() => new Promise((resolve) => { landWrite = resolve; }));
    const road = () => wrapper.findAllComponents(ListItemButton).find((c) => c.props('title') === PLAYLIST.name);
    road().vm.$emit('click');
    await flushPromises();

    await wrapper.setProps({ isOpen: false, songIds: [] });
    await wrapper.setProps({ isOpen: true, songIds: ['second'] });
    await flushPromises();
    answers[1](ok({ playlist_ids: [] }));  // "second" is in no playlist
    await flushPromises();
    landWrite(ok({ status: 'success' }));
    await flushPromises();

    road().vm.$emit('click');
    await flushPromises();

    const [url, body] = apiCall.put.mock.calls.at(-1);
    expect(url).toBe(`/api/music-library/playlist/${PLAYLIST.id}`);
    expect(body).toEqual({ song_ids_to_add: ['second'] });
  });
});
