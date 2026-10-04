// frontend/tests/composables/useAutoPlayer.test.js
/**
 * useAutoPlayer opens a browser source's full player once the unit's own
 * screen has gone untouched for the configured delay during playback.
 *
 * Every condition is asserted on its own, because each one left out is a
 * screen that changes under someone: a phone holding the UI (not the kiosk),
 * a source with no navigation, a paused session, lyrics being read — and a
 * load between two tracks is not one of them, or the delay would restart at
 * every track.
 * Then the two ways the countdown restarts from zero — a touch, and the
 * conditions holding again after one fell (the back button included) — and
 * the one thing it must never do: close the player when playback pauses.
 *
 * The stores are the real ones, driven through the handler the WebSocket calls.
 * A host component is mounted only to give the composable a lifecycle; nothing
 * is rendered or asserted on the DOM.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { defineComponent, h, nextTick } from 'vue';
import { mount } from '@vue/test-utils';

const kiosk = vi.hoisted(() => ({ value: true }));

vi.mock('@/services/apiCall', () => import('../helpers/apiCallMock'));
vi.mock('@/utils/kiosk', () => ({ isKiosk: () => kiosk.value }));

import { useAutoPlayer } from '@/composables/useAutoPlayer';
import { usePlayerExpansion } from '@/composables/usePlayerExpansion';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useSettingsStore } from '@/stores/settingsStore';
import { useLyricsStore } from '@/stores/lyricsStore';
import { makeSession, publishState } from '../helpers/audioState';

const DELAY_SECONDS = 10;
const DELAY_MS = DELAY_SECONDS * 1000;

function radio(phase = 'playing') {
  return {
    source: 'radio',
    service: 'running',
    session: makeSession({ phase, title: 'Le Code a changé' }),
    controls: phase === 'playing' ? ['stop'] : ['resume_playback'],
  };
}

/** Let `ms` pass, with the watchers that a store write woke settled first. */
async function advance(ms) {
  await nextTick();
  vi.advanceTimersByTime(ms);
  await nextTick();
}

describe('useAutoPlayer', () => {
  let store;
  let expansion;
  let wrapper;

  function mountAutoPlayer() {
    const Host = defineComponent({
      setup() {
        useAutoPlayer();
        return () => h('div');
      },
    });
    wrapper = mount(Host);
  }

  beforeEach(() => {
    vi.useFakeTimers();
    kiosk.value = true;
    store = useUnifiedAudioStore();
    useSettingsStore().screenAutoPlayer = {
      auto_player_enabled: true,
      auto_player_delay_seconds: DELAY_SECONDS,
    };
    publishState(store, radio());
    expansion = usePlayerExpansion();
    // Module state: a previous test may have left the player open.
    expansion.collapse();
  });

  afterEach(() => {
    wrapper?.unmount();
    wrapper = null;
    vi.useRealTimers();
  });

  it('opens the full player once the delay passes untouched during playback', async () => {
    mountAutoPlayer();

    await advance(DELAY_MS - 1);
    expect(expansion.expanded.value).toBe(false);

    await advance(1);
    expect(expansion.expanded.value).toBe(true);
  });

  it('counts the delay again from a touch', async () => {
    mountAutoPlayer();
    await advance(DELAY_MS - 2000);

    document.dispatchEvent(new Event('pointerdown'));
    await advance(DELAY_MS - 1);
    expect(expansion.expanded.value).toBe(false);

    await advance(1);
    expect(expansion.expanded.value).toBe(true);
  });

  it('never arms off the unit\'s own screen', async () => {
    kiosk.value = false;
    mountAutoPlayer();

    await advance(DELAY_MS * 3);
    expect(expansion.expanded.value).toBe(false);
  });

  it('never arms with the setting off', async () => {
    useSettingsStore().screenAutoPlayer.auto_player_enabled = false;
    mountAutoPlayer();

    await advance(DELAY_MS * 3);
    expect(expansion.expanded.value).toBe(false);
  });

  it('never arms for a source with no navigation to leave', async () => {
    publishState(store, {
      source: 'cd',
      service: 'running',
      session: makeSession({ title: 'Track 1' }),
      controls: ['pause', 'next', 'prev', 'seek'],
    });
    mountAutoPlayer();

    await advance(DELAY_MS * 3);
    expect(expansion.expanded.value).toBe(false);
  });

  it('never arms while the session is paused', async () => {
    publishState(store, radio('paused'));
    mountAutoPlayer();

    await advance(DELAY_MS * 3);
    expect(expansion.expanded.value).toBe(false);
  });

  it('keeps counting through a track change, so a delay longer than a track is reached', async () => {
    mountAutoPlayer();
    await advance(DELAY_MS - 2000);

    publishState(store, radio('loading'));
    await advance(1000);
    publishState(store, radio('playing'));
    await advance(999);
    expect(expansion.expanded.value).toBe(false);

    await advance(1);
    expect(expansion.expanded.value).toBe(true);
  });

  it('never arms while the lyrics are open', async () => {
    useLyricsStore().open();
    mountAutoPlayer();

    await advance(DELAY_MS * 3);
    expect(expansion.expanded.value).toBe(false);
  });

  it('stops counting when a condition falls, and starts over when it holds again', async () => {
    mountAutoPlayer();
    await advance(DELAY_MS - 1000);

    useLyricsStore().open();
    await advance(DELAY_MS * 3);
    expect(expansion.expanded.value).toBe(false);

    useLyricsStore().close();
    await advance(DELAY_MS - 1);
    expect(expansion.expanded.value).toBe(false);

    await advance(1);
    expect(expansion.expanded.value).toBe(true);
  });

  it('opens the player again after the back button, once the delay has passed again', async () => {
    mountAutoPlayer();
    await advance(DELAY_MS);
    expect(expansion.expanded.value).toBe(true);

    expansion.collapse();
    await advance(DELAY_MS - 1);
    expect(expansion.expanded.value).toBe(false);

    await advance(1);
    expect(expansion.expanded.value).toBe(true);
  });

  it('leaves the player on screen when playback pauses', async () => {
    mountAutoPlayer();
    await advance(DELAY_MS);
    expect(expansion.expanded.value).toBe(true);

    publishState(store, radio('paused'));
    await advance(DELAY_MS * 3);
    expect(expansion.expanded.value).toBe(true);
  });

  it('counts a new delay from the moment it is set', async () => {
    mountAutoPlayer();
    await advance(DELAY_MS - 1000);

    useSettingsStore().screenAutoPlayer.auto_player_delay_seconds = DELAY_SECONDS * 2;
    await advance(DELAY_MS * 2 - 1);
    expect(expansion.expanded.value).toBe(false);

    await advance(1);
    expect(expansion.expanded.value).toBe(true);
  });
});
