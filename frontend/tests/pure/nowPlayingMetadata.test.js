// frontend/tests/pure/nowPlayingMetadata.test.js
/**
 * What AudioPlayerFull and the screensaver name: which record they read, and
 * what the player keeps as its last-known snapshot. Only the branches that
 * decide something are asserted — handing the helper a full record and reading
 * the keys back would assert the language, not the rule.
 *
 * What breaks when this fails: a CD with no session shows what play would
 * bring back (its resume record), so a reader of the session alone leaves the
 * player on "Unknown Title" over a disc it can play; a reader that ignores the
 * selected source draws another source's track in a player that is leaving;
 * and a disc no lookup identified has no artist, so a snapshot demanding one
 * never stores the title the tracklist lists.
 */
import { describe, it, expect } from 'vitest';
import { nowPlayingOf, nowPlayingSnapshot } from '@/utils/nowPlayingMetadata';
import { makeAudioState, makeSession } from '../helpers/audioState';

const RESUME = {
  title: 'So What', artist: 'Miles Davis', album: 'Kind of Blue', artwork: null,
  duration_ms: 562000, position_ms: 0,
};

describe('nowPlayingOf', () => {
  it('reads the session while one runs, over any resume point', () => {
    const session = makeSession({ title: 'Blue in Green' });
    const state = makeAudioState({ source: 'cd', session, resume: RESUME });

    expect(nowPlayingOf(state, 'cd')).toBe(session);
  });

  it('falls back to what play would bring back when no session runs', () => {
    const state = makeAudioState({ source: 'cd', resume: RESUME });

    expect(nowPlayingOf(state, 'cd')).toBe(RESUME);
  });

  it('gives nothing to a view whose source is not the selected one', () => {
    const state = makeAudioState({ source: 'spotify', session: makeSession({ title: 'Hyperballad' }) });

    expect(nowPlayingOf(state, 'cd')).toBeNull();
  });
});

describe('nowPlayingSnapshot', () => {
  it('keeps the track title of a disc no lookup identified', () => {
    const snap = nowPlayingSnapshot(makeSession({ title: 'Track 3', artist: null }));

    expect(snap).not.toBeNull();
    expect(snap.title).toBe('Track 3');
    // '' and not null: AudioPlayerFull builds `${title}|${artist}` as the key
    // rearming the artwork transition, so the absent artist must not stringify.
    expect(snap.artist).toBe('');
  });

  it('refuses a record with nothing to name, so the previous snapshot stands', () => {
    // A sender still connecting publishes a session with no title.
    expect(nowPlayingSnapshot(makeSession({ phase: 'connected', senders: ['iPhone'] }))).toBeNull();
    expect(nowPlayingSnapshot(null)).toBeNull();
  });
});
