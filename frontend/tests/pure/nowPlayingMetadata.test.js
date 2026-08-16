// frontend/tests/pure/nowPlayingMetadata.test.js
/**
 * The rule deciding what AudioPlayerFull keeps as its last-known now-playing
 * snapshot. Only the branches that decide something are asserted — handing the
 * helper a full metadata dict and reading the keys back would assert the
 * language, not the rule.
 *
 * What breaks when this fails: a CD MusicBrainz cannot identify has no artist,
 * so a rule demanding one leaves the snapshot on its empty seed and the player
 * shows "Unknown Title" over a disc whose track titles it knows (and whose
 * tracklist behind the queue button lists them). The opposite failure is the
 * reason the pair is still required for the other five sources.
 */
import { describe, it, expect } from 'vitest';
import { nowPlayingSnapshot } from '@/utils/nowPlayingMetadata';

describe('nowPlayingSnapshot', () => {
  it('keeps the track title of a disc no lookup identified', () => {
    // What _build_fallback_disc_info publishes: real title, artist=None.
    const snap = nowPlayingSnapshot('cd', { title: 'Track 3', artist: null, disc_present: true });

    expect(snap).not.toBeNull();
    expect(snap.title).toBe('Track 3');
    // '' and not null: AudioPlayerFull builds `${title}|${artist}` as the key
    // rearming the artwork transition, so the absent artist must not stringify.
    expect(snap.artist).toBe('');
  });

  it('refuses a half-populated update from a source gated on both fields', () => {
    // Spotify/Tidal/Qobuz/AirPlay/DLNA only reach this player with an artist,
    // so a snapshot missing one is mid-update — keeping it would blank an
    // artist the previous snapshot has.
    expect(nowPlayingSnapshot('spotify', { title: 'Says' })).toBeNull();
  });

  it('refuses metadata with nothing to show, whatever the source', () => {
    expect(nowPlayingSnapshot('cd', { disc_present: true, cache_ready: true })).toBeNull();
    expect(nowPlayingSnapshot('spotify', {})).toBeNull();
  });

  it('survives the metadata being absent entirely', () => {
    // The watcher runs with `immediate: true`, before the store's first payload.
    expect(nowPlayingSnapshot('cd', null)).toBeNull();
  });
});
