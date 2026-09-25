// frontend/tests/pure/volumeConversion.test.js
/**
 * dB → percent is the seam between what the backend broadcasts (dB) and what
 * the sliders/meters render (percent). It is pure arithmetic with two ranges in
 * play (the Snapcast -72→0 default and LevelMeter's -60→0), so it is cheap to
 * test and expensive to get subtly wrong.
 */
import { describe, it, expect } from 'vitest';
import { dbToPercent } from '@/constants/volumeConversion';

describe('dbToPercent', () => {
  it('maps the default Snapcast range onto 0…100', () => {
    expect(dbToPercent(-72)).toBe(0);
    expect(dbToPercent(0)).toBe(100);
    expect(dbToPercent(-36)).toBe(50);
  });

  it('clamps out-of-range input instead of overflowing the slider', () => {
    expect(dbToPercent(-100)).toBe(0);
    expect(dbToPercent(12)).toBe(100);
  });

  it('returns whole percents', () => {
    expect(Number.isInteger(dbToPercent(-25.4))).toBe(true);
    expect(dbToPercent(-25.4)).toBe(65);
  });

  it('honours an explicit range (LevelMeter uses -60 → 0 dBFS)', () => {
    expect(dbToPercent(-60, -60, 0)).toBe(0);
    expect(dbToPercent(-30, -60, 0)).toBe(50);
    expect(dbToPercent(0, -60, 0)).toBe(100);
  });
});
