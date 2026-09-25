// frontend/tests/composables/useRichDisplay.test.js
/**
 * `useRichDisplay` decides, in one place, whether the screen shows a source's
 * full view or the AudioSourceStatus card. Two consumers read it —
 * AudioSourceView mounts the component, MainView hides the logo — so the rule
 * living here is what keeps those two from drifting.
 *
 * What is covered is the *gates*: the service, the switch, the source's
 * availability, the browser sources' standing view and the session phase. Each
 * one takes the player away or gives it back, and taking a player away is how a
 * control disappears from under a finger.
 *
 * The store is the real one, driven through the handler the WebSocket calls.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { setActivePinia, createPinia } from 'pinia';

vi.mock('@/services/apiCall', () => import('../helpers/apiCallMock'));

import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useRichDisplay } from '@/composables/useRichDisplay';
import { makeSession, publishState } from '../helpers/audioState';

const PLAYING = makeSession({ title: 'Future Green', artist: 'Masahiro Sugaya' });
const PAUSED = { ...PLAYING, phase: 'paused' };

describe('useRichDisplay', () => {
  let store;
  let richSource;

  function publish(overrides) {
    publishState(store, { source: 'spotify', service: 'running', ...overrides });
  }

  beforeEach(() => {
    setActivePinia(createPinia());
    store = useUnifiedAudioStore();
    ({ richSource } = useRichDisplay());
  });

  it('shows the player for a source with a session it can name', () => {
    publish({ session: PLAYING });

    expect(richSource.value).toBe('spotify');
  });

  it('shows the card for a running source with nothing to name', () => {
    publish({ session: null });

    expect(richSource.value).toBeNull();
  });

  it('falls back to the card when the link blocks the source and nothing is playing', () => {
    publish({ session: PAUSED, availability: { spotify: 'no_internet' } });

    expect(richSource.value).toBeNull();
  });

  it('keeps the player while sound is still coming out', () => {
    // The appliance must never show a screen with no way to stop the music it
    // is playing. A buffered stream survives the link dropping — go-librespot
    // holds a whole track — and swapping in the status card there deletes the
    // only control that could stop it. Measured with the cable out, 2026-09-04.
    publish({ session: PLAYING, availability: { spotify: 'no_internet' } });

    expect(richSource.value).toBe('spotify');
  });

  it('lets the card arrive by itself when the source finally gives up', () => {
    publish({ session: PLAYING, availability: { spotify: 'no_internet' } });
    expect(richSource.value).toBe('spotify');

    publish({ session: PAUSED, availability: { spotify: 'no_internet' } });

    expect(richSource.value).toBeNull();
  });

  it('never shows a player for a failed service', () => {
    publish({
      service: 'failed',
      service_error: { reason: 'start_failed', message: 'go-librespot exited' },
      session: null,
    });

    expect(richSource.value).toBeNull();
  });

  it('defers to the card while a switch is in flight', () => {
    publish({ session: PLAYING, switching: true });

    expect(richSource.value).toBeNull();
  });

  it('shows the card for a sender Milō cannot tell playing from paused, with nothing to show', () => {
    // "Connecté à X" is the card's sentence while the sender names nothing.
    publishState(store, {
      source: 'bluetooth',
      service: 'running',
      session: makeSession({ phase: 'connected', title: 'Track', senders: ['iPhone'] }),
    });

    expect(richSource.value).toBeNull();
  });

  it('shows the player for a connected sender naming a cover, a title and an artist (D14)', () => {
    // An iPhone's AirPlay stream stayed connected from start to end while it
    // named every track (measured 2026-09-25): the card hid all of it.
    const named = { title: 'Pas vraiment', artist: 'Malik Djoudi', artwork: '/api/airplay/artwork?v=1' };
    publishState(store, {
      source: 'bluetooth',
      service: 'running',
      session: makeSession({ phase: 'connected', senders: ['iPhone'], ...named }),
    });
    expect(richSource.value).toBe('bluetooth');

    for (const missing of ['title', 'artist', 'artwork']) {
      publishState(store, {
        source: 'bluetooth',
        service: 'running',
        session: makeSession({ phase: 'connected', senders: ['iPhone'], ...named, [missing]: null }),
      });
      expect(richSource.value, missing).toBeNull();
    }
  });

  it('shows a browser source its own view with nothing playing (D13)', () => {
    // Radio, Podcast and the Music Library are where the first thing to play is
    // chosen: the card would put the browser out of reach.
    for (const source of ['radio', 'podcast', 'music_library']) {
      publishState(store, { source, service: 'running', session: null });
      expect(richSource.value).toBe(source);
    }
  });

  it('lets the Music Library draw its own storage state (D13)', () => {
    publishState(store, {
      source: 'music_library',
      service: 'running',
      availability: { music_library: 'no_storage' },
    });

    expect(richSource.value).toBe('music_library');
  });

  it('takes a browser source off its view when its link is gone', () => {
    publishState(store, {
      source: 'radio',
      service: 'running',
      availability: { radio: 'no_internet' },
    });

    expect(richSource.value).toBeNull();
  });

  it('shows the CD player on a disc it can name before play is pressed', () => {
    publishState(store, {
      source: 'cd',
      service: 'running',
      resume: { title: 'Track 1', artist: 'Artist', album: 'Album', artwork: null, duration_ms: 1000, position_ms: 0 },
    });

    expect(richSource.value).toBe('cd');
  });

  it("gives an untrusted AirPlay sender's tiny cover to the card", () => {
    const airplay = { source: 'airplay', service: 'running', session: PLAYING };
    publishState(store, { ...airplay, details: { kind: 'airplay', artwork_width: 64 } });
    expect(richSource.value).toBeNull();

    publishState(store, { ...airplay, details: { kind: 'airplay', artwork_width: 600 } });
    expect(richSource.value).toBe('airplay');
  });
});
