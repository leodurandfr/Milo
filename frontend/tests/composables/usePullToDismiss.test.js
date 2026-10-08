// frontend/tests/composables/usePullToDismiss.test.js
/**
 * `pullOutcome` decides whether the phone's pull down on the full player sends
 * it back to the navigation or springs it back. A short slow pull dismissing
 * would close the player on a stray touch; a deliberate flick not dismissing
 * would leave the gesture working only on long, slow drags.
 */
import { describe, it, expect } from 'vitest';
import { pullOutcome } from '@/composables/usePullToDismiss';

const HEIGHT = 800;

describe('pullOutcome', () => {
  it('dismisses a pull past a quarter of the view, however slow', () => {
    expect(pullOutcome({ dy: 201, durationMs: 5000, height: HEIGHT })).toBe('dismiss');
  });

  it('dismisses a quick flick well short of a quarter', () => {
    expect(pullOutcome({ dy: 60, durationMs: 80, height: HEIGHT })).toBe('dismiss');
  });

  it('springs back a short slow pull', () => {
    expect(pullOutcome({ dy: 120, durationMs: 1000, height: HEIGHT })).toBe('cancel');
  });

  it('springs back a flick too short to be meant', () => {
    expect(pullOutcome({ dy: 30, durationMs: 10, height: HEIGHT })).toBe('cancel');
  });

  it('springs back a pull that came back up past its start', () => {
    expect(pullOutcome({ dy: -50, durationMs: 50, height: HEIGHT })).toBe('cancel');
    expect(pullOutcome({ dy: 0, durationMs: 50, height: HEIGHT })).toBe('cancel');
  });
});
