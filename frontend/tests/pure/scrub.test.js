// frontend/tests/pure/scrub.test.js
/**
 * utils/scrub.js turns a pointer on the progress bar into the position a seek
 * aims at. A drag holds the pointer past the bar's ends, so a failure here is a
 * seek sent before the start or beyond the track's end, or a bar measured
 * before it has a size seeking to NaN.
 */
import { describe, it, expect } from 'vitest';
import { seekPositionAt } from '@/utils/scrub';

describe('seekPositionAt', () => {
  it('reads the fraction of the track under the pointer', () => {
    expect(seekPositionAt(150, 100, 200, 240000)).toBe(60000);
  });

  it('holds the ends when the pointer leaves the bar', () => {
    expect(seekPositionAt(20, 100, 200, 240000)).toBe(0);
    expect(seekPositionAt(900, 100, 200, 240000)).toBe(240000);
  });

  it('answers whole milliseconds, the wire unit', () => {
    expect(Number.isInteger(seekPositionAt(101, 100, 3, 1000))).toBe(true);
  });

  it('aims at the start when there is no track to measure', () => {
    expect(seekPositionAt(150, 100, 0, 240000)).toBe(0);
    expect(seekPositionAt(150, 100, 200, 0)).toBe(0);
  });
});
