// frontend/tests/composables/usePlayerExpansion.test.js
/**
 * `usePlayerExpansion` says whether a browser source (radio, podcast, music
 * library, Spotify) shows its navigation or the full player it expands into,
 * and `useExpandedView(source)` is the view BrowserSourceViews draws from it.
 *
 * Three rules are pinned. Another source opens on its own default view, so a
 * change of source drops the flag — left standing, switching from an expanded
 * radio to the music library would open the library on its player instead of
 * its catalog. A pause on the same source keeps the player: only the back
 * button, or an ending with nothing to resume (BrowserSourceViews), returns
 * to the navigation. And a source on its way out keeps the view it had — dropping to
 * the navigation mid-leave cross-faded the player away and, on the phone,
 * brought the teleported bar in over the status card.
 *
 * One exception to the change of source: a return within the window to the
 * source left on its full player is a source picked by mistake, and opens that
 * player again once the source has settled — never before (it would open on
 * an empty player while the source starts), never past the window, never for
 * a source left on its navigation, and never on a source with nothing kept.
 *
 * The store is the real one, driven through the handler the WebSocket calls.
 * The flag is module state, so every test starts collapsed; the source rule is
 * held once per store, so each test's fresh Pinia gets it bound again.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { setActivePinia, createPinia } from 'pinia';
import { effectScope, nextTick } from 'vue';

vi.mock('@/services/apiCall', () => import('../helpers/apiCallMock'));

import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { usePlayerExpansion, useExpandedView, RETURN_WINDOW_MS } from '@/composables/usePlayerExpansion';
import { makeSession, publishState } from '../helpers/audioState';

const PLAYING = makeSession({ title: 'Says', artist: 'Nils Frahm' });

describe('usePlayerExpansion', () => {
  let store;
  let scope;
  let expansion;
  let radioView;

  function publish(source, overrides = {}) {
    publishState(store, { source, service: 'running', session: PLAYING, ...overrides });
  }

  beforeEach(() => {
    setActivePinia(createPinia());
    store = useUnifiedAudioStore();
    publish('radio');
    scope = effectScope();
    scope.run(() => {
      expansion = usePlayerExpansion();
      radioView = useExpandedView('radio');
    });
    expansion.collapse();
  });

  afterEach(() => {
    scope.stop();
  });

  it('draws the full player once expanded, and the navigation once collapsed', async () => {
    expect(radioView.value).toBe(false);

    expansion.expand();
    await nextTick();
    expect(radioView.value).toBe(true);

    expansion.collapse();
    await nextTick();
    expect(radioView.value).toBe(false);
  });

  it('collapses when the active source changes', async () => {
    expansion.expand();

    publish('music_library');
    await nextTick();

    expect(expansion.expanded.value).toBe(false);
  });

  it('stays expanded through a pause on the same source', async () => {
    expansion.expand();
    await nextTick();

    publish('radio', { session: { ...PLAYING, phase: 'paused' } });
    await nextTick();

    expect(expansion.expanded.value).toBe(true);
    expect(radioView.value).toBe(true);
  });

  it('keeps the player on screen while the source it belongs to is being left', async () => {
    expansion.expand();
    await nextTick();

    publish('music_library');
    await nextTick();

    expect(radioView.value).toBe(true);
  });

  it('opens a source mounted after a change on its navigation', async () => {
    expansion.expand();
    publish('music_library');
    await nextTick();

    const libraryView = scope.run(() => useExpandedView('music_library'));
    expect(libraryView.value).toBe(false);
  });

  describe('a return within the window', () => {
    const STATION = { title: 'FIP', artist: null, album: 'FIP', artwork: null, duration_ms: null, position_ms: null };

    // A switch as the backend publishes it: the selection first, with nothing
    // to show while the source starts, then the state that settles it.
    async function switchTo(source, settled = {}) {
      publishState(store, { source, service: 'starting', switching: true, session: null });
      await nextTick();
      publish(source, settled);
      await nextTick();
    }

    beforeEach(() => {
      vi.useFakeTimers({ toFake: ['Date'] });
    });

    afterEach(() => {
      vi.useRealTimers();
    });

    it('opens the full player again on what the source kept, once it has settled', async () => {
      expansion.expand();
      await switchTo('music_library');

      publishState(store, { source: 'radio', service: 'starting', switching: true, session: null });
      await nextTick();
      expect(expansion.expanded.value).toBe(false);

      publish('radio', { session: null, resume: STATION });
      await nextTick();
      expect(expansion.expanded.value).toBe(true);
      expect(radioView.value).toBe(true);
    });

    it('opens the navigation past the window', async () => {
      expansion.expand();
      await switchTo('music_library');

      vi.setSystemTime(Date.now() + RETURN_WINDOW_MS);
      await switchTo('radio', { session: null, resume: STATION });

      expect(expansion.expanded.value).toBe(false);
    });

    it('opens the navigation when the source was left on it', async () => {
      await switchTo('music_library');
      await switchTo('radio', { session: null, resume: STATION });

      expect(expansion.expanded.value).toBe(false);
    });

    it('opens the navigation when the source kept nothing', async () => {
      publish('spotify');
      await nextTick();
      expansion.expand();
      await switchTo('radio');
      await switchTo('spotify', { session: null, resume: null });

      expect(expansion.expanded.value).toBe(false);
    });

    it('is spent by the first settled state, not kept for a later one', async () => {
      publish('spotify');
      await nextTick();
      expansion.expand();
      await switchTo('radio');
      await switchTo('spotify', { session: null, resume: null });

      publish('spotify');
      await nextTick();

      expect(expansion.expanded.value).toBe(false);
    });
  });
});
