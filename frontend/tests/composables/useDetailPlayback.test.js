// frontend/tests/composables/useDetailPlayback.test.js
/**
 * useDetailPlayback is the play button of every detail page's header (an
 * album, a playlist, an artist, an episode). What breaks: a header spinning
 * for a track change pressed on the player (its queue is still the page's,
 * and passes through `loading`), a paused page restarted from its top instead
 * of resumed, and a spinner left turning after a press that never plays.
 *
 * The real store is driven through its own `source/state` handler and its
 * commands reach the apiCall mock; a host component is mounted only for the
 * composable's lifecycle.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { defineComponent, h, nextTick } from 'vue';
import { mount } from '@vue/test-utils';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useDetailPlayback } from '@/composables/useDetailPlayback';
import { apiCall } from '@/services/apiCall';
import { resetApiCallMock, ok, fail } from '../helpers/apiCallMock';
import { publishState, makeSession } from '../helpers/audioState';

vi.mock('@/services/apiCall', () => import('../helpers/apiCallMock'));

const PAGE = 'album:spaces';
const OTHER = 'album:felt';

describe('useDetailPlayback', () => {
  let store;

  function mountHeader() {
    let header;
    mount(defineComponent({
      setup() {
        header = useDetailPlayback(
          'music_library',
          () => store.systemState.details?.context_id === PAGE,
          () => store.sendCommand('music_library', 'play_context', { context_id: PAGE }),
        );
        return () => h('div');
      },
    }));
    return header;
  }

  const publish = (contextId, phase, controls = []) => publishState(store, {
    source: 'music_library', service: 'running', controls,
    session: phase ? makeSession({ id: contextId, phase }) : null,
    details: contextId ? { kind: 'music_library', context_id: contextId } : null,
  });

  const commandsSent = () =>
    apiCall.post.mock.calls
      .filter(([url]) => url === '/api/audio/control/music_library')
      .map(([, body]) => body.command);

  async function settle() {
    await nextTick();
    await Promise.resolve();
    await nextTick();
  }

  beforeEach(() => {
    resetApiCallMock();
    apiCall.post.mockResolvedValue(ok({ status: 'success' }));
    store = useUnifiedAudioStore();
  });

  it('does not spin for a load nobody pressed it for', async () => {
    const header = mountHeader();
    publish(PAGE, 'playing');
    await nextTick();
    expect(header.playing.value).toBe(true);

    publish(PAGE, 'loading');
    await nextTick();

    expect(header.loading.value).toBe(false);
  });

  it('spins from a press until the page plays, whatever plays meanwhile', async () => {
    const header = mountHeader();
    publish(OTHER, 'playing');
    await nextTick();
    expect(header.playing.value).toBe(false);

    header.play();
    await settle();
    expect(commandsSent()).toEqual(['play_context']);
    expect(header.loading.value).toBe(true);

    publish(OTHER, 'playing');
    await nextTick();
    expect(header.loading.value).toBe(true);
    publish(PAGE, 'loading');
    await nextTick();
    expect(header.loading.value).toBe(true);

    publish(PAGE, 'playing');
    await nextTick();
    expect(header.loading.value).toBe(false);
    expect(header.playing.value).toBe(true);
  });

  it('resumes a paused page rather than starting it over', async () => {
    const header = mountHeader();
    publish(PAGE, 'paused', ['resume', 'stop']);
    await nextTick();

    header.play();
    await settle();

    expect(commandsSent()).toEqual(['resume']);
  });

  it('pauses the page that plays', async () => {
    const header = mountHeader();
    publish(PAGE, 'playing', ['pause']);
    await nextTick();

    header.pause();
    await settle();

    expect(commandsSent()).toEqual(['pause']);
  });

  it('stops spinning when the press is refused', async () => {
    const header = mountHeader();
    apiCall.post.mockResolvedValueOnce(fail('Music library catalog not ready'));

    header.play();
    await settle();

    expect(header.loading.value).toBe(false);
  });

  it('stops spinning when the load it started ends without playing', async () => {
    const header = mountHeader();
    header.play();
    await settle();
    publish(PAGE, 'loading');
    await nextTick();
    expect(header.loading.value).toBe(true);

    publish(null, null);
    await nextTick();

    expect(header.loading.value).toBe(false);
  });
});
