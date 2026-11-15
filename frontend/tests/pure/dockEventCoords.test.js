/**
 * The Dock reads a gesture's coordinates from mouse, pointer and touch events
 * alike. If this fails, a touch at the screen's left or top edge reads as
 * `undefined`: the swipe from the reveal band there opens nothing, and the
 * movement it measures is NaN, so it never cancels a hold.
 */
import { describe, it, expect } from 'vitest';
import { getEventX, getEventY } from '@/composables/useDockDrag';

describe('Dock event coordinates', () => {
  it('reads 0 from a touch at the screen\'s edge', () => {
    const start = { type: 'touchstart', touches: [{ clientX: 0, clientY: 0 }] };
    expect(getEventX(start)).toBe(0);
    expect(getEventY(start)).toBe(0);

    const end = { type: 'touchend', touches: [], changedTouches: [{ clientX: 0, clientY: 0 }] };
    expect(getEventX(end)).toBe(0);
    expect(getEventY(end)).toBe(0);
  });

  it('reads a pointer or a mouse event from its own coordinates', () => {
    expect(getEventX({ type: 'pointerdown', pointerType: 'touch', clientX: 0, clientY: 5 })).toBe(0);
    expect(getEventY({ type: 'mousedown', clientX: 3, clientY: 0 })).toBe(0);
  });
});
