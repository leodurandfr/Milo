// frontend/tests/pure/spotifyRepeat.test.js
/**
 * The repeat button walks off → context → track → off, as the Spotify app's
 * does; a mode the cycle skipped would be a state one button can never reach.
 */
import { describe, it, expect } from 'vitest';
import { REPEAT_MODES, nextRepeatMode } from '@/utils/spotifyRepeat';

describe('nextRepeatMode', () => {
  it('reaches every mode once and comes back', () => {
    const seen = [];
    let mode = 'off';
    for (let i = 0; i < REPEAT_MODES.length; i += 1) {
      seen.push(mode);
      mode = nextRepeatMode(mode);
    }
    expect(seen).toEqual(['off', 'context', 'track']);
    expect(mode).toBe('off');
  });
});
