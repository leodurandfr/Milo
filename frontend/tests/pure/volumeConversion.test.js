// frontend/tests/pure/volumeConversion.test.js
/**
 * dB → percent of a meter's span: what LevelMeter draws the output peak with.
 * Pure arithmetic, cheap to test and expensive to get subtly wrong.
 */
import { describe, it, expect } from 'vitest';
import { dbToPercent } from '@/utils/volumeConversion';

describe('dbToPercent', () => {
  it('maps the span onto 0…100 (LevelMeter: -60 → 0 dBFS)', () => {
    expect(dbToPercent(-60, -60, 0)).toBe(0);
    expect(dbToPercent(-30, -60, 0)).toBe(50);
    expect(dbToPercent(0, -60, 0)).toBe(100);
  });

  it('clamps out-of-range input instead of overflowing the meter', () => {
    expect(dbToPercent(-100, -60, 0)).toBe(0);
    expect(dbToPercent(12, -60, 0)).toBe(100);
  });

  it('returns whole percents', () => {
    expect(Number.isInteger(dbToPercent(-25.4, -60, 0))).toBe(true);
    expect(dbToPercent(-25.4, -60, 0)).toBe(58);
  });

  it('takes any span, not only the meter\'s', () => {
    expect(dbToPercent(-36, -72, 0)).toBe(50);
  });
});
