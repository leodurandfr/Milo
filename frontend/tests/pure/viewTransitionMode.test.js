// frontend/tests/pure/viewTransitionMode.test.js
/**
 * Whether a navigation keeps the scroll decides whether the NavigationHeader
 * moves: a 'move' while the header is on screen at both ends drops it into place
 * in one frame, and a 'keep' that cannot hold the scroll lets the browser clamp
 * it at the end of the cross-fade — the same jump, one transition later.
 */
import { describe, it, expect } from 'vitest';
import { navigationMode, keepOutcome } from '@/composables/useViewTransition';

const H = 100; // the header's bottom edge

describe('navigationMode', () => {
  it('keeps the scroll while the header is on screen before and after', () => {
    expect(navigationMode({ scroll: 30, target: 0, headerBottom: H })).toBe('keep');
    expect(navigationMode({ scroll: H - 1, target: 0, headerBottom: H })).toBe('keep');
    // Back from the top to a page saved a little scrolled: stays at the top.
    expect(navigationMode({ scroll: 0, target: 40, headerBottom: H })).toBe('keep');
  });

  it('moves the scroll when the header is off screen at either end', () => {
    expect(navigationMode({ scroll: H, target: 0, headerBottom: H })).toBe('move');
    expect(navigationMode({ scroll: 30, target: H, headerBottom: H })).toBe('move');
    expect(navigationMode({ scroll: 500, target: 40, headerBottom: H })).toBe('move');
  });

  it('has nothing to keep when the scroll does not change', () => {
    expect(navigationMode({ scroll: 0, target: 0, headerBottom: H })).toBe('move');
  });

  it('never keeps without a header to hold still', () => {
    expect(navigationMode({ scroll: 30, target: 0, headerBottom: null })).toBe('move');
  });
});

describe('keepOutcome', () => {
  it('needs nothing when the destination holds the scroll', () => {
    expect(keepOutcome(30, 30)).toBe('none');
    expect(keepOutcome(30, 800)).toBe('none');
  });

  it('reserves the missing height when the destination scrolls, but not that far', () => {
    expect(keepOutcome(30, 29)).toBe('reserve');
    expect(keepOutcome(30, 0)).toBe('reserve');
    // A min-height: 100% wrapper can round one pixel under its scroller.
    expect(keepOutcome(30, -1)).toBe('reserve');
  });

  it('glides when the destination fits with no scroll at all', () => {
    expect(keepOutcome(30, -2)).toBe('glide');
    expect(keepOutcome(30, -400)).toBe('glide');
  });
});
