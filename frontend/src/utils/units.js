// frontend/src/utils/units.js
/**
 * A value with its unit, written the way the UI language writes it.
 *
 * The rules differ per language and are CLDR's, read through Intl rather than
 * restated here: French and German space the percent sign (42 %) where English
 * and Italian do not (42%), English writes 52.3°C where the European languages
 * write 52,3 °C, German abbreviates with a period (10 Sek.), French says Mo and
 * ko/s, and five of the eight languages take a decimal comma. A unit concatenated
 * by hand in a component is right in one language at best.
 */
import { bcp47For } from '@/constants/countries';

/** Units CLDR knows, keyed by their English symbol. */
const CLDR_UNITS = {
  '%': 'percent',
  '°C': 'celsius',
  ms: 'millisecond',
  s: 'second',
  min: 'minute',
  h: 'hour',
  bit: 'bit',
  MB: 'megabyte',
  GB: 'gigabyte',
  'B/s': 'byte-per-second',
  'kB/s': 'kilobyte-per-second',
  'MB/s': 'megabyte-per-second',
  'kbit/s': 'kilobit-per-second',
};

/**
 * SI symbols CLDR has no unit for. The SI rule — a space before the symbol —
 * is the same in all eight languages, so they need no table.
 */
const SI_SYMBOLS = ['dB', 'Hz', 'kHz'];

/** A compressor ratio: no space, the digits only follow the language. */
const RATIO = ':1';

/** Every unit formatUnit() accepts. */
export const UNITS = [...Object.keys(CLDR_UNITS), ...SI_SYMBOLS, RATIO];

// CLDR spaces a unit with U+0020, U+00A0 or U+202F, sometimes all three within
// one language. All become a no-break space: a value never wraps away from its
// unit, and Neue Montreal has no glyph for U+202F, which French uses before
// every unit — "52,3 °C" rendered as "52,3°C".
const toNoBreak = (text) => text.replace(/\s/g, ' ');

const formatters = new Map();

function formatterFor(Kind, locale, options) {
  const key = `${Kind.name}|${locale}|${JSON.stringify(options)}`;
  let formatter = formatters.get(key);
  if (!formatter) {
    formatter = new Kind(locale, options);
    formatters.set(key, formatter);
  }
  return formatter;
}

/**
 * A bare number in the language's digits: decimal comma or point, thousands
 * grouping. `options` are Intl.NumberFormat's (fraction digits, signDisplay).
 */
export function formatNumber(value, language, options = {}) {
  return toNoBreak(formatterFor(Intl.NumberFormat, bcp47For(language), options).format(value));
}

/**
 * `value` in `unit` (one of UNITS), e.g. formatUnit(52.3, '°C', 'french') →
 * "52,3 °C". `options` are Intl.NumberFormat's.
 */
export function formatUnit(value, unit, language, options = {}) {
  if (unit === RATIO) return `${formatNumber(value, language, options)}${RATIO}`;
  if (SI_SYMBOLS.includes(unit)) return `${formatNumber(value, language, options)} ${unit}`;

  const cldrUnit = CLDR_UNITS[unit];
  if (!cldrUnit) throw new Error(`formatUnit: unknown unit "${unit}"`);
  const formatter = formatterFor(Intl.NumberFormat, bcp47For(language), { ...options, style: 'unit', unit: cldrUnit });
  return toNoBreak(formatter.format(value));
}

/**
 * A duration in seconds in the language's short form: "1 hr, 5 min" in English,
 * "1 h et 5 min" in French, "1 Std., 5 Min." in German. `smallest: 'minute'`
 * drops the seconds (a podcast's remaining time).
 */
export function formatDuration(totalSeconds, language, { smallest = 'second' } = {}) {
  const total = Math.max(0, Math.floor(totalSeconds || 0));
  const fields = {
    hours: Math.floor(total / 3600),
    minutes: Math.floor((total % 3600) / 60),
    ...(smallest === 'second' && { seconds: total % 60 }),
  };
  // DurationFormat omits zero fields, and so a zero duration entirely.
  if (!Object.values(fields).some(Boolean)) {
    return formatUnit(0, smallest === 'second' ? 's' : 'min', language);
  }
  return toNoBreak(formatterFor(Intl.DurationFormat, bcp47For(language), { style: 'short' }).format(fields));
}
