// frontend/tests/pure/playbackBuffering.test.js
/**
 * The buffering rule shared by AudioPlayerFull and LyricsPlaybackBar: get it
 * wrong and the play button either spins forever or offers "play" on a source
 * that cannot yet play. The CD clause is the part worth pinning — it fires on
 * metadata that reports nothing about buffering at all.
 */
import { describe, it, expect } from 'vitest';
import { isSourceBuffering } from '@/utils/playbackBuffering';

describe('isSourceBuffering', () => {
  it('follows is_buffering for any source', () => {
    expect(isSourceBuffering('spotify', { is_buffering: true })).toBe(true);
    expect(isSourceBuffering('spotify', { is_buffering: false })).toBe(false);
  });

  it('tolerates absent metadata', () => {
    expect(isSourceBuffering('spotify', null)).toBe(false);
    expect(isSourceBuffering('spotify', {})).toBe(false);
  });

  it('keeps CD buffering while the disc identity is still resolving', () => {
    // Player is already on screen (disc_present) but the MusicBrainz lookup
    // has not produced a disc_id yet — nothing is playable.
    expect(isSourceBuffering('cd', { disc_present: true, cache_ready: true })).toBe(true);
    expect(isSourceBuffering('cd', { disc_present: true, disc_id: 'abc' })).toBe(true);
  });

  it('clears CD buffering once the disc is identified and cached', () => {
    expect(isSourceBuffering('cd', {
      disc_present: true, cache_ready: true, disc_id: 'abc',
    })).toBe(false);
  });

  it('does not apply the CD clause to an empty drive or another source', () => {
    expect(isSourceBuffering('cd', { disc_present: false })).toBe(false);
    expect(isSourceBuffering('music_library', { disc_present: true })).toBe(false);
  });
});
