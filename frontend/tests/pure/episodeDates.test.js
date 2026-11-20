// frontend/tests/pure/episodeDates.test.js
/**
 * What an episode card says about time (`useEpisodePlaybackStatus`): "Today",
 * "Yesterday", else the date, and the time left.
 *
 * Goes red if "Today" and "Yesterday" go back to counting 24-hour slices (an
 * episode out last night read "Today" this morning, one from the evening
 * before yesterday read "Yesterday"), if the date is assembled by hand again
 * ("17 11月" in Chinese, "17 Nov" in English), or if the time left truncates to
 * minutes below the last one ("0 min left" for an episode not over).
 */
import { describe, it, expect } from 'vitest';
import { calendarDaysBetween, formatEpisodeDay, formatRemaining } from '@/utils/episodeDates';

describe('calendarDaysBetween', () => {
  it('counts last night as yesterday, though fewer than 24 hours have passed', () => {
    expect(calendarDaysBetween(new Date(2026, 10, 16, 23, 0), new Date(2026, 10, 17, 8, 0))).toBe(1);
  });

  it('counts the evening before yesterday as two days, though fewer than 48 hours have passed', () => {
    expect(calendarDaysBetween(new Date(2026, 10, 15, 22, 0), new Date(2026, 10, 17, 8, 0))).toBe(2);
  });

  it('counts this morning as today from late in the evening', () => {
    expect(calendarDaysBetween(new Date(2026, 10, 17, 0, 5), new Date(2026, 10, 17, 23, 55))).toBe(0);
  });

  it('counts a day across a change of clock as one day', () => {
    // 2026-03-29 is 23 hours long in Europe and 2026-03-08 in North America,
    // so the midnight-to-midnight span after each switch is 23 hours, and only
    // the rounding counts it as a day. The suite runs in Europe/Paris
    // (vitest.config.js), so the first pair is checked everywhere, CI
    // included; the second where a North American zone runs it.
    expect(calendarDaysBetween(new Date(2026, 2, 29, 12, 0), new Date(2026, 2, 30, 12, 0))).toBe(1);
    expect(calendarDaysBetween(new Date(2026, 2, 8, 12, 0), new Date(2026, 2, 9, 12, 0))).toBe(1);
  });
});

describe('formatEpisodeDay', () => {
  const day = new Date(2026, 10, 17, 12, 0);

  it('writes the date in each language’s own order', () => {
    expect(formatEpisodeDay(day, 'english')).toBe('Nov 17');
    expect(formatEpisodeDay(day, 'chinese')).toBe('11月17日');
    expect(formatEpisodeDay(day, 'french')).toBe('17 nov.');
    expect(formatEpisodeDay(day, 'german')).toBe('17. Nov.');
  });
});

describe('formatRemaining', () => {
  it('counts the last minute in seconds, never as no minute left', () => {
    expect(formatRemaining(42, 'english')).toMatch(/^42\ssec$/u);
    expect(formatRemaining(6, 'english')).toMatch(/^6\ssec$/u);
  });

  it('counts in whole minutes from the last one up, with no seconds', () => {
    expect(formatRemaining(60, 'english')).toMatch(/^1\smin$/u);
    expect(formatRemaining(3_725, 'english')).toMatch(/^1\shr,\s2\smin$/u);
  });
});
