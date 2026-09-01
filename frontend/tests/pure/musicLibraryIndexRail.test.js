// frontend/tests/pure/musicLibraryIndexRail.test.js
/**
 * The Artists rail is abridged whenever the band is too short for the index, and
 * the pointer is mapped over that abridged strip — so the one rule that has to
 * hold is that a press answers with the letter printed under it. It did not
 * when the mapping ran over the full index instead: tapping M scrolled to L,
 * which reads as a scroll bug rather than an index one.
 */
import { describe, it, expect } from 'vitest';
import { condenseLetters, letterAtRatio } from '@/components/music-library/indexRail';

const AZ = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'.split('');

describe('condenseLetters', () => {
  it('draws the index untouched when it fits', () => {
    expect(condenseLetters(AZ, 26)).toEqual(AZ);
    expect(condenseLetters(AZ, 40)).toEqual(AZ);
  });

  it('never draws more rows than the rail has', () => {
    for (let slots = 1; slots <= AZ.length; slots += 1) {
      expect(condenseLetters(AZ, slots).length).toBeLessThanOrEqual(slots);
    }
  });

  it('keeps both ends and samples evenly in between', () => {
    expect(condenseLetters(AZ, 13))
      .toEqual(['A', 'C', 'E', 'G', 'I', 'K', 'N', 'P', 'R', 'T', 'V', 'X', 'Z']);
    expect(condenseLetters(AZ, 7)).toEqual(['A', 'E', 'I', 'N', 'R', 'V', 'Z']);
  });

  it('spends every row it has on a letter', () => {
    // The abridgement is at most every other letter here, so a rail that gave
    // half its rows to \"something was dropped\" markers would be what forced the
    // harsh sampling the markers announce.
    for (let slots = 3; slots < AZ.length; slots += 1) {
      const drawn = condenseLetters(AZ, slots);
      expect(drawn.length).toBe(slots);
      expect(drawn.every((letter) => AZ.includes(letter))).toBe(true);
    }
  });

  it('never draws a letter out of order', () => {
    const drawn = condenseLetters(AZ, 11);
    expect([...drawn].sort()).toEqual(drawn);
  });

  it('degrades to a single letter rather than dividing by zero', () => {
    expect(condenseLetters(AZ, 2)).toEqual(['A', 'Z']);
    expect(condenseLetters(AZ, 1)).toEqual(['A']);
  });

  it('draws nothing before a height has been measured', () => {
    // An unscaled strip on the first frame would overflow the rail's cap.
    expect(condenseLetters(AZ, 0)).toEqual([]);
    expect(condenseLetters([], 20)).toEqual([]);
    expect(condenseLetters(undefined, 20)).toEqual([]);
  });
});

describe('letterAtRatio', () => {
  it('splits the rail into one equal band per letter', () => {
    expect(letterAtRatio(AZ, 0)).toBe('A');
    expect(letterAtRatio(AZ, 0.5)).toBe('N');
    expect(letterAtRatio(AZ, 0.999)).toBe('Z');
    // Band boundaries: 1/26 is exactly where A hands over to B.
    expect(letterAtRatio(AZ, 1 / 26 - 0.001)).toBe('A');
    expect(letterAtRatio(AZ, 1 / 26)).toBe('B');
  });

  it('clamps past both ends, so a finger sliding off keeps scrubbing', () => {
    expect(letterAtRatio(AZ, -3)).toBe('A');
    expect(letterAtRatio(AZ, 1)).toBe('Z');
    expect(letterAtRatio(AZ, 12)).toBe('Z');
  });

  it('has no answer without letters or without a measurable rail', () => {
    expect(letterAtRatio([], 0.5)).toBeNull();
    expect(letterAtRatio(AZ, NaN)).toBeNull();
    expect(letterAtRatio(AZ, Infinity)).toBeNull();
  });
});

describe('rail geometry — a press answers with the letter under it', () => {
  // Anywhere on a rung, not just its middle: a finger lands where it lands, and
  // the top and bottom edges are where an off-by-one rung would show.
  it.each([3, 7, 11, 13, 26])('holds on a strip of %i rows', (slots) => {
    const drawn = condenseLetters(AZ, slots);
    drawn.forEach((letter, i) => {
      for (const within of [0.01, 0.5, 0.99]) {
        expect(letterAtRatio(drawn, (i + within) / drawn.length)).toBe(letter);
      }
    });
  });

  it('keeps both ends of the index one press away, however short the rail', () => {
    // What the abridgement drops is reached from its neighbour and a short
    // scroll — but the first and last buckets are the two nothing neighbours.
    for (let slots = 2; slots <= AZ.length; slots += 1) {
      const drawn = condenseLetters(AZ, slots);
      expect(letterAtRatio(drawn, 0)).toBe('A');
      expect(letterAtRatio(drawn, 0.999)).toBe('Z');
    }
  });
});
