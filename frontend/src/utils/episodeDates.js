// frontend/src/utils/episodeDates.js
/**
 * What an episode card says about time: when the episode came out, and how
 * much of it is left.
 */
import { bcp47For } from '@/constants/countries';
import { formatDuration } from '@/utils/units';

const DAY_MS = 86_400_000;

/**
 * Calendar days from `date` back to `now`, in local time: 0 on the same day,
 * 1 the day before, whatever the hours — an episode out yesterday at 23:00 is
 * yesterday's at 08:00, not "today" because fewer than 24 hours have passed.
 * Rounded, so a day that a DST change made 23 or 25 hours long still counts
 * as one.
 */
export function calendarDaysBetween(date, now) {
  const midnight = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
  return Math.round((midnight(now) - midnight(date)) / DAY_MS);
}

const dayFormatters = new Map();

/**
 * The day and short month, in the order and spelling the language writes them
 * (CLDR's, through Intl): "Nov 17", "17 nov.", "17. Nov.", "11月17日".
 */
export function formatEpisodeDay(date, language) {
  const locale = bcp47For(language);
  let formatter = dayFormatters.get(locale);
  if (!formatter) {
    formatter = new Intl.DateTimeFormat(locale, { day: 'numeric', month: 'short' });
    dayFormatters.set(locale, formatter);
  }
  return formatter.format(date);
}

/**
 * The time left in an episode: whole minutes, then seconds under the last
 * one — minutes alone would announce "0 min" left in an episode that is not
 * over.
 */
export function formatRemaining(seconds, language) {
  return formatDuration(seconds, language, seconds < 60 ? {} : { smallest: 'minute' });
}
