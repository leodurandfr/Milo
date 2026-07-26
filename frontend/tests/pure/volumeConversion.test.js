// frontend/tests/pure/volumeConversion.test.js
/**
 * dB ↔ percent is the seam between what the backend broadcasts (dB) and what
 * the sliders/meters render (percent). It is pure arithmetic with two ranges in
 * play (the Snapcast -72→0 default and LevelMeter's -60→0), so it is cheap to
 * test and expensive to get subtly wrong.
 */
import { describe, it, expect } from 'vitest';
import { dbToPercent, percentToDb } from '@/constants/volumeConversion';

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

describe('percentToDb', () => {
  it('maps 0…100 back onto the default range', () => {
    expect(percentToDb(0)).toBe(-72);
    expect(percentToDb(100)).toBe(0);
    expect(percentToDb(50)).toBe(-36);
  });

  it('clamps out-of-range percentages', () => {
    expect(percentToDb(-10)).toBe(-72);
    expect(percentToDb(150)).toBe(0);
  });

  it('honours an explicit range', () => {
    expect(percentToDb(50, -60, 0)).toBe(-30);
  });
});

describe('round trip', () => {
  it('returns to the same percent through both conversions', () => {
    for (const percent of [0, 17, 33, 50, 66, 99, 100]) {
      expect(dbToPercent(percentToDb(percent))).toBe(percent);
    }
  });

  it('stays within a percent of the original dB value', () => {
    // dbToPercent rounds, so the round trip is lossy by at most one step.
    for (const db of [-72, -60, -45, -30, -12, 0]) {
      expect(Math.abs(percentToDb(dbToPercent(db)) - db)).toBeLessThan(0.72);
    }
  });
});
