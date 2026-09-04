// frontend/tests/composables/useRichDisplay.test.js
/**
 * `useRichDisplay` decides, in one place, whether the screen shows a source's
 * rich player or the AudioSourceStatus card. Two consumers read it —
 * AudioSourceView mounts the component, MainView hides the logo — so the rule
 * living here is what keeps those two from drifting.
 *
 * What is covered is the *gates*, not the per-source metadata tables: the
 * error gate, the network-unavailable gate, and the transition gate. Each one
 * takes the player away, and taking a player away is how a control disappears
 * from under a finger.
 *
 * The store is the real one, driven through the handler the WebSocket calls.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { setActivePinia, createPinia } from 'pinia';

vi.mock('@/services/apiCall', () => import('../helpers/apiCallMock'));

import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useRichDisplay } from '@/composables/useRichDisplay';

/** A `system.state_changed` envelope, as the backend broadcasts it. */
function publish(store, fullState) {
  store.updateState({
    data: {
      full_state: {
        active_source: 'spotify',
        source_state: 'active',
        transitioning: false,
        multiroom_enabled: true,
        equalizer_effects_enabled: true,
        network_unavailable: null,
        ...fullState,
      },
    },
  });
}

const PLAYING = { title: 'Future Green', artist: 'Masahiro Sugaya', is_playing: true };
const PAUSED = { ...PLAYING, is_playing: false };

describe('useRichDisplay', () => {
  let store;
  let richSource;

  beforeEach(() => {
    setActivePinia(createPinia());
    store = useUnifiedAudioStore();
    ({ richSource } = useRichDisplay());
  });

  it('shows the player for a source that has what it needs', () => {
    publish(store, { metadata: PLAYING });

    expect(richSource.value).toBe('spotify');
  });

  it('falls back to the card when the link blocks the source and nothing is playing', () => {
    publish(store, { metadata: PAUSED, network_unavailable: 'no_internet' });

    expect(richSource.value).toBeNull();
  });

  it('keeps the player while sound is still coming out', () => {
    // The appliance must never show a screen with no way to stop the music it
    // is playing. A buffered stream survives the link dropping — go-librespot
    // holds a whole track — and swapping in the status card there deletes the
    // only control that could stop it. Measured with the cable out, 2026-09-04.
    publish(store, { metadata: PLAYING, network_unavailable: 'no_internet' });

    expect(richSource.value).toBe('spotify');
  });

  it('lets the card arrive by itself when the source finally gives up', () => {
    // The sequel to the case above, and the reason cutting the audio on the
    // network signal is not needed: mpv reports EOF, go-librespot disconnects,
    // is_playing goes false, and this is the same rule one beat later.
    publish(store, { metadata: PLAYING, network_unavailable: 'no_internet' });
    expect(richSource.value).toBe('spotify');

    publish(store, { metadata: PAUSED, network_unavailable: 'no_internet' });

    expect(richSource.value).toBeNull();
  });

  it('never shows a player for an errored source, playing or not', () => {
    // ERROR is stronger than the link: nothing the source could draw is true
    // any more, so is_playing must not buy it a player back.
    publish(store, { metadata: PLAYING, source_state: 'error' });

    expect(richSource.value).toBeNull();
  });

  it('defers to the card while a transition is in flight', () => {
    publish(store, { metadata: PLAYING, transitioning: true });

    expect(richSource.value).toBeNull();
  });
});
