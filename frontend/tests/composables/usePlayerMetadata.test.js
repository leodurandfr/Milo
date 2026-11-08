// frontend/tests/composables/usePlayerMetadata.test.js
/**
 * The station intro: a radio session opened while the page watched is shown
 * as the station alone — its name, its logo, no detected-song source bar — for
 * the store's STATION_INTRO_MS, then as the record the wire carries. Radio
 * France names the song before the first sound, so without it the station a
 * tune just reached was never seen at all. Both players read their own copy of
 * this composable (AudioPlayer, AudioPlayerFull), and the two must flip on the
 * same tick, or the bar and the full player name two records for a moment.
 *
 * Driven through the store's own handler, as App.vue's `source/state` row
 * does. That the clock runs for radio alone is the store's rule, tested there.
 *
 * A host component is mounted only to give the composable a lifecycle;
 * nothing is rendered or asserted on the DOM.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { defineComponent, h, nextTick } from 'vue';
import { mount } from '@vue/test-utils';
import { usePlayerMetadata } from '@/composables/usePlayerMetadata';
import { useUnifiedAudioStore, STATION_INTRO_MS } from '@/stores/unifiedAudioStore';
import { makeAudioState, makeSession, publishState } from '../helpers/audioState';

vi.mock('@/services/apiCall', () => import('../helpers/apiCallMock'));

// A directory logo, external like Radio Browser's, with the characters
// encodeURIComponent and the backend's quote() spell differently.
const STATION = { name: 'FIP', favicon: 'https://www.radiofrance.fr/fip_(2021).png' };
// The session's logo as the backend writes it on the wire (_resolved_favicon):
// through the proxy, which those hosts' WAFs require.
const STATION_LOGO = '/api/radio/favicon?url=https%3A%2F%2Fwww.radiofrance.fr%2Ffip_%282021%29.png';
const SONG = { title: 'Snibor', artist: 'Gil Evans', artwork: 'https://covers.test/snibor.jpg' };

/** The radio's state as the backend publishes it: the song on the session when one is named. */
function radio({ id = 'tune-1', song = SONG, pending = false } = {}) {
  return {
    source: 'radio',
    service: 'running',
    session: makeSession({
      id,
      title: song ? song.title : STATION.name,
      artist: song ? song.artist : null,
      album: STATION.name,
      artwork: song?.artwork || STATION_LOGO,
    }),
    controls: ['stop'],
    details: { kind: 'radio', station: STATION, track: song, artwork_pending: pending },
  };
}

function mountMetadata(source) {
  let metadata;
  const Host = defineComponent({
    setup() {
      metadata = usePlayerMetadata(source);
      return () => h('div');
    },
  });
  mount(Host);
  return metadata;
}

describe('usePlayerMetadata — the station intro', () => {
  let store;

  beforeEach(() => {
    vi.useFakeTimers();
    store = useUnifiedAudioStore();
  });

  afterEach(() => vi.useRealTimers());

  it('a tune that already names a song shows the station until STATION_INTRO_MS, then the song', async () => {
    const player = mountMetadata('radio');
    publishState(store, radio());
    await nextTick();

    expect(player.title.value).toBe('FIP');
    expect(player.artwork.value).toBe(STATION_LOGO);
    expect(player.sourceOnCard.value).toBe(false);
    expect(player.secondaryLine.value).toBe('');

    vi.advanceTimersByTime(STATION_INTRO_MS - 1);
    await nextTick();
    expect(player.title.value).toBe('FIP');

    vi.advanceTimersByTime(1);
    await nextTick();
    expect(player.title.value).toBe('Snibor');
    expect(player.artwork.value).toBe(SONG.artwork);
    expect(player.secondaryLine.value).toBe('Gil Evans');
    expect(player.sourceOnCard.value).toBe(true);
  });

  it('names one logo URL through the intro and after it, on a station with no song', async () => {
    // Spelled apart, the same logo is fetched again at the intro's end and
    // faded into itself.
    const player = mountMetadata('radio');
    publishState(store, radio({ song: null }));
    await nextTick();
    expect(player.artwork.value).toBe(store.systemState.session.artwork);

    vi.advanceTimersByTime(STATION_INTRO_MS);
    await nextTick();
    expect(player.artwork.value).toBe(store.systemState.session.artwork);
  });

  it('a song named after the intro shows at once', async () => {
    const player = mountMetadata('radio');
    publishState(store, radio({ song: null }));
    vi.advanceTimersByTime(3000);
    publishState(store, radio());
    await nextTick();

    expect(player.title.value).toBe('Snibor');
  });

  it('a session first seen in a snapshot is never held', async () => {
    const player = mountMetadata('radio');
    store.updateState({ category: 'system', type: 'initial_state', data: { state: makeAudioState(radio()) } });
    await nextTick();

    expect(player.title.value).toBe('Snibor');
  });

  it('announces no cover during the intro, then the one the song waits for', async () => {
    // Announced during the intro, the hold would keep the previous station's
    // logo where this one's belongs.
    const player = mountMetadata('radio');
    publishState(store, radio({ song: { ...SONG, artwork: null }, pending: true }));
    await nextTick();
    expect(player.artworkAnnounced.value).toBe(false);

    vi.advanceTimersByTime(STATION_INTRO_MS);
    await nextTick();
    expect(player.artworkAnnounced.value).toBe(true);
  });

  it('two players flip on the same tick', async () => {
    const bar = mountMetadata('radio');
    publishState(store, radio());
    vi.advanceTimersByTime(1000);
    // The full player opened mid-intro still ends it with the bar.
    const full = mountMetadata('radio');
    await nextTick();
    expect([bar.title.value, full.title.value]).toEqual(['FIP', 'FIP']);

    vi.advanceTimersByTime(STATION_INTRO_MS - 1000);
    await nextTick();
    expect([bar.title.value, full.title.value]).toEqual(['Snibor', 'Snibor']);
    expect(bar.trackKey.value).toBe(full.trackKey.value);
  });
});
