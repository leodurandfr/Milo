// frontend/tests/pure/keyboardGeometry.test.js
/**
 * Popup placement for the kiosk virtual keyboard. A misplaced popup covers the
 * wrong key, and a wrong accent index inserts a letter nobody slid to.
 *
 * The assertions are relations (centred, inside the keyboard, offset-invariant),
 * not copies of the expressions under test.
 */
import { describe, it, expect } from 'vitest';
import {
  pressPopupPlacement,
  accentPopupPlacement,
  accentPopupWidth,
  accentIndexAt
} from '@/components/ui/keyboard/geometry';

/** A DOMRect-alike, in whatever px space the caller is describing. */
const rect = ({ left = 0, width = 0, top = 0, bottom = 0 }) => ({ left, width, top, bottom });

const KB = rect({ left: 40, width: 800, bottom: 620 });
const KEY = rect({ left: 340, width: 76, top: 420 });

describe('pressPopupPlacement', () => {
  it('centres the popup on the key', () => {
    const { left, width } = pressPopupPlacement(KEY, KB);
    const keyCentre = (KEY.left - KB.left) + KEY.width / 2;
    expect(left + width / 2).toBeCloseTo(keyCentre);
  });

  it('sits above the key', () => {
    const { bottom } = pressPopupPlacement(KEY, KB);
    expect(bottom).toBeGreaterThan(KB.bottom - KEY.top);
  });

  it('is always wider than the key, so the enlarged glyph has room', () => {
    const narrow = pressPopupPlacement(rect({ left: 340, width: 8, top: 420 }), KB);
    const wide = pressPopupPlacement(KEY, KB);
    expect(narrow.width).toBeGreaterThan(8);
    expect(wide.width).toBeGreaterThan(KEY.width);
    expect(wide.width).toBeGreaterThanOrEqual(narrow.width);
  });

});

describe('accentPopupPlacement', () => {
  it('centres on the key when there is room either side', () => {
    const { left } = accentPopupPlacement(KEY, KB, 3);
    const width = accentPopupWidth(3);
    const keyCentre = (KEY.left - KB.left) + KEY.width / 2;
    expect(left + width / 2).toBeCloseTo(keyCentre);
  });

  it('keeps the popup inside the keyboard at both edges', () => {
    // 4 variants is the widest accent row shipped (french "e").
    const width = accentPopupWidth(4);
    const leftmost = accentPopupPlacement(rect({ left: KB.left, width: 76, top: 420 }), KB, 4);
    const rightmost = accentPopupPlacement(
      rect({ left: KB.left + KB.width - 76, width: 76, top: 420 }), KB, 4
    );
    expect(leftmost.left).toBeGreaterThanOrEqual(0);
    expect(rightmost.left + width).toBeLessThanOrEqual(KB.width);
  });

  it('widens with the number of variants', () => {
    expect(accentPopupWidth(4)).toBeGreaterThan(accentPopupWidth(1));
  });

});

describe('accentIndexAt', () => {
  /**
   * Walk a finger across the popup one px at a time and record what each
   * position selects.
   */
  const sweep = (variantCount, { kbLeft = 0, popupLeft = 0 } = {}) => {
    const indices = [];
    for (let x = 0; x < accentPopupWidth(variantCount); x += 1) {
      indices.push(accentIndexAt(kbLeft + popupLeft + x, kbLeft, popupLeft, variantCount));
    }
    return indices;
  };

  it('selects every variant, left to right, without going backwards', () => {
    const indices = sweep(4);
    expect(indices[0]).toBe(0);
    expect(indices[indices.length - 1]).toBe(3);
    expect([...new Set(indices)].sort()).toEqual([0, 1, 2, 3]);
    expect(indices).toEqual([...indices].sort((a, b) => a - b));
  });

  it('selects the same variant at any keyboard offset or popup offset', () => {
    const base = sweep(4);
    expect(sweep(4, { kbLeft: 120, popupLeft: 37 })).toEqual(base);
    expect(sweep(4, { kbLeft: 8, popupLeft: 210 })).toEqual(base);
  });

  it('clamps a finger dragged off either end', () => {
    expect(accentIndexAt(-5000, 0, 0, 3)).toBe(0);
    expect(accentIndexAt(5000, 0, 0, 3)).toBe(2);
  });

  it('never returns an index the variant row does not have', () => {
    expect(accentIndexAt(5000, 0, 0, 1)).toBe(0);
  });
});
