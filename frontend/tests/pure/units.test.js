// frontend/tests/pure/units.test.js
/**
 * utils/units.js writes a value with its unit the way each UI language does.
 *
 * The rules are CLDR's, read through Intl, so these cases pin what Milō adds on
 * top — the no-break space, the European Portuguese tag, the SI symbols CLDR has
 * no unit for, the zero duration — plus one example per rule a reader would
 * otherwise "fix" back to English (the spaced percent sign in French, the
 * period in German's Sek.). A failure here is a unit drawn wrong on screen.
 */
import { describe, it, expect } from 'vitest';
import { readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { UNITS, formatUnit, formatNumber, formatDuration } from '@/utils/units';
import { bcp47For } from '@/constants/countries';

const LOCALES_DIR = resolve(dirname(fileURLToPath(import.meta.url)), '../../src/locales');
const LANGUAGES = readdirSync(LOCALES_DIR).filter(f => f.endsWith('.json')).map(f => f.replace('.json', ''));

const NB = ' ';

describe('formatUnit', () => {
  it('follows each language\'s spacing and abbreviations', () => {
    expect(formatUnit(42, '%', 'english')).toBe('42%');
    expect(formatUnit(42, '%', 'french')).toBe(`42${NB}%`);
    expect(formatUnit(42, '%', 'italian')).toBe('42%');
    expect(formatUnit(52.3, '°C', 'english')).toBe('52.3°C');
    expect(formatUnit(52.3, '°C', 'german')).toBe(`52,3${NB}°C`);
    expect(formatUnit(10, 's', 'german')).toBe(`10${NB}Sek.`);
    expect(formatUnit(5, 'MB', 'french')).toBe(`5${NB}Mo`);
  });

  it('spaces the SI symbols CLDR lacks the same way everywhere, with the language\'s digits', () => {
    expect(formatUnit(-2.5, 'dB', 'english')).toBe(`-2.5${NB}dB`);
    expect(formatUnit(-2.5, 'dB', 'french')).toBe(`-2,5${NB}dB`);
    expect(formatUnit(44.1, 'kHz', 'chinese')).toBe(`44.1${NB}kHz`);
    expect(formatUnit(4, ':1', 'english')).toBe('4:1');
  });

  it('never lets a value wrap away from its unit, in any language', () => {
    // Neue Montreal has no glyph for U+202F, which French puts before every
    // unit: left in, "52,3 °C" renders as "52,3°C".
    const outputs = LANGUAGES.flatMap(language => UNITS.map(unit => formatUnit(1234.5, unit, language)));
    expect(outputs.length).toBeGreaterThan(50);
    for (const text of outputs) expect(text).not.toMatch(/[  ]/);
  });

  it('refuses a unit it does not know rather than printing it bare', () => {
    expect(() => formatUnit(3, 'dBFS', 'english')).toThrow(/dBFS/);
  });
});

describe('formatNumber', () => {
  it('writes the language\'s decimal separator and the sign it is asked for', () => {
    const gain = { signDisplay: 'exceptZero', minimumFractionDigits: 1 };
    expect(formatNumber(2.5, 'english', gain)).toBe('+2.5');
    expect(formatNumber(2.5, 'portuguese', gain)).toBe('+2,5');
    expect(formatNumber(0, 'french', gain)).toBe('0,0');
  });
});

describe('formatDuration', () => {
  it('writes the short form of the language', () => {
    expect(formatDuration(3900, 'english')).toBe(`1${NB}hr,${NB}5${NB}min`);
    expect(formatDuration(3900, 'french')).toBe(`1${NB}h${NB}et${NB}5${NB}min`);
    expect(formatDuration(30, 'english')).toBe(`30${NB}sec`);
  });

  it('drops the seconds when asked for minutes', () => {
    expect(formatDuration(3930, 'english', { smallest: 'minute' })).toBe(formatDuration(3900, 'english'));
  });

  it('writes a zero duration out, which Intl.DurationFormat leaves empty', () => {
    expect(formatDuration(0, 'english')).toBe(`0${NB}sec`);
    expect(formatDuration(20, 'english', { smallest: 'minute' })).toBe(`0${NB}min`);
    expect(formatDuration(undefined, 'french')).toBe(`0${NB}s`);
  });
});

describe('the Portuguese tag', () => {
  it('resolves to Portugal: portuguese.json is European Portuguese, a bare "pt" is Brazil', () => {
    expect(new Intl.NumberFormat(bcp47For('portuguese')).resolvedOptions().locale).toBe('pt-PT');
  });
});
