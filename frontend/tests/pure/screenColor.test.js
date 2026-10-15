// frontend/tests/pure/screenColor.test.js
/**
 * utils/screenColor.js decides the kiosk's one color layer: the panel's
 * measured correction times the warm filter. A failure here is a kiosk drawing
 * a full-screen blend for nothing (GPU cost, measured), or a warm filter that
 * no longer does on a corrected panel what it did on a raw one.
 */
import { describe, it, expect } from 'vitest';
import { screenColorGains } from '@/utils/screenColor';

const CORRECTION = [1, 0.875, 0.768];

describe('screenColorGains', () => {
  it('asks for no layer when nothing changes the panel', () => {
    expect(screenColorGains(null, { enabled: false, warmth: 80 })).toBeNull();
    expect(screenColorGains(null, { enabled: true, warmth: 0 })).toBeNull();
    expect(screenColorGains(null, null)).toBeNull();
  });

  it('never yields a NaN gain from a filter without a warmth', () => {
    expect(screenColorGains(null, { enabled: true })).toBeNull();
    expect(screenColorGains(CORRECTION, { enabled: true, warmth: null })).toEqual(CORRECTION);
  });

  it('keeps the correction when the filter is off', () => {
    expect(screenColorGains(CORRECTION, { enabled: false, warmth: 80 })).toEqual(CORRECTION);
  });

  it('composes the filter over the correction by product, at every warmth', () => {
    for (const warmth of [10, 50, 100]) {
      const filter = { enabled: true, warmth };
      const alone = screenColorGains(null, filter);
      const composed = screenColorGains(CORRECTION, filter);
      composed.forEach((gain, i) => expect(gain).toBeCloseTo(CORRECTION[i] * alone[i], 10));
    }
  });

  it('warms by attenuating only: red stays whole, blue falls before green', () => {
    const [r, g, b] = screenColorGains(null, { enabled: true, warmth: 100 });
    expect(r).toBe(1);
    expect(b).toBeLessThan(g);
    expect(g).toBeLessThan(1);
  });
});
