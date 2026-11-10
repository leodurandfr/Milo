// frontend/tests/pure/screenInset.test.js
/**
 * utils/screenInset.js turns the kiosk's bezel setting into --screen-inset. A
 * failure here is content pushed by a different strip than the one the bezel
 * hides — after a ui_scale change most of all — or an edge drawn between two
 * panel pixels.
 */
import { describe, it, expect } from 'vitest';
import { screenInsetPx } from '@/utils/screenInset';

const PITCH = 1280 / 172.3;

describe('screenInsetPx', () => {
  it('draws no inset when there is none to draw', () => {
    expect(screenInsetPx(0, PITCH, 1.25)).toBeNull();
    expect(screenInsetPx(1.5, null, 1.25)).toBeNull();
    expect(screenInsetPx(1.5, PITCH, 0)).toBeNull();
    expect(screenInsetPx(0.05, PITCH, 1.25)).toBeNull();
  });

  it('covers a whole number of panel pixels', () => {
    for (const mm of [0.25, 1, 1.5, 2.75, 3]) {
      for (const ratio of [1, 1.1, 1.2, 1.25, 1.3]) {
        const panelPixels = screenInsetPx(mm, PITCH, ratio) * ratio;
        expect(panelPixels).toBeCloseTo(Math.round(panelPixels), 9);
        expect(Math.abs(panelPixels / PITCH - mm)).toBeLessThanOrEqual(0.5 / PITCH);
      }
    }
  });

  it('keeps the same strip of glass at every interface scale', () => {
    const atScale = (ratio) => Math.round(screenInsetPx(1.5, PITCH, ratio) * ratio * 1e6);
    expect(new Set([1, 1.1, 1.2, 1.25, 1.3].map(atScale)).size).toBe(1);
  });
});
