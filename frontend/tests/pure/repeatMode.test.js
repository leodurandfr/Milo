// frontend/tests/pure/repeatMode.test.js
/**
 * The repeat button walks off → context → track → off; a mode the cycle
 * skipped would be a state one button can never reach.
 */
import { describe, it, expect } from 'vitest';
import { REPEAT_MODES, nextRepeatMode } from '@/utils/repeatMode';

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
