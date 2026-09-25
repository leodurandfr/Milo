// frontend/tests/stores/cdStore.test.js
/**
 * cdStore derives the disc and the playing track from the audio state's CD
 * details. What breaks if it fails: CDSource's tracklist and LyricsPlaybackBar's
 * "next" read an empty disc, or the tracklist marks a track playing while paused.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useCdStore } from '@/stores/cdStore';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { resetApiCallMock } from '../helpers/apiCallMock';
import { publishState, makeSession } from '../helpers/audioState';

vi.mock('@/services/apiCall', () => import('../helpers/apiCallMock'));

const DISC = {
  id: 'disc-1', album: 'Mezzanine', artist: 'Massive Attack', year: '1998', cover_url: null,
  tracks: [
    { number: 1, title: 'Angel', duration_ms: 379000 },
    { number: 2, title: 'Risingson', duration_ms: 298000 },
  ],
};

describe('cdStore', () => {
  let store;
  let unified;

  beforeEach(() => {
    resetApiCallMock();
    unified = useUnifiedAudioStore();
    store = useCdStore();
  });

  it('reads the disc and the current track from the details, session or not', () => {
    publishState(unified, {
      source: 'cd', service: 'running',
      details: { kind: 'cd', disc: DISC, current_track: 2, artwork_pending: false },
    });
    expect(store.discInfo.album).toBe('Mezzanine');
    expect(store.tracks).toHaveLength(2);
    expect(store.currentTrack).toBe(2);
    expect(store.isPlaying).toBe(false);

    publishState(unified, {
      source: 'cd', service: 'running', session: makeSession({ phase: 'playing' }),
      details: { kind: 'cd', disc: DISC, current_track: 2, artwork_pending: false },
    });
    expect(store.isPlaying).toBe(true);
  });

  it('has no disc for an unreadable one, nor once another source is selected', () => {
    publishState(unified, {
      source: 'cd', service: 'running',
      details: { kind: 'cd', disc: null, current_track: null, artwork_pending: false },
    });
    expect(unified.systemState.details?.kind).toBe('cd');
    expect(store.discInfo).toBeNull();
    expect(store.tracks).toEqual([]);

    publishState(unified, { source: 'radio', service: 'running', session: makeSession() });
    expect(unified.systemState.source).toBe('radio');
    expect(store.discInfo).toBeNull();
    expect(store.isPlaying).toBe(false);
  });
});
