// frontend/tests/architecture/unitFormatting.test.js
/**
 * A value with a unit reaches the screen through utils/units.js, never by
 * appending the unit by hand.
 *
 * Every hand-written unit was right in one language at best: a slider showed
 * "+3dB" beside another showing "8 dB", a countdown read "12s remaining" in
 * English and "Il reste 12 s" in French, "5 MB" stayed "MB" in French (Mo), and
 * "52.3°C" kept its point in the five languages with a decimal comma. So two
 * places may not carry a unit next to a value any more:
 *
 *   1. a locale string — a placeholder followed by a unit ("{seconds} s") or a
 *      number followed by one ("85 °C"). The value arrives already formatted,
 *      as one parameter ("{time}", "{temperature}");
 *   2. a component or composable — an interpolation followed by a unit
 *      (`{{ x }} ms`, `${x} kbps`).
 *
 * CSS is out of scope — `${x}ms` in a transition is not text — so <style> blocks
 * are dropped, and in script only units that never appear in CSS are matched.
 * The gallery is excluded: it is English developer documentation, not the UI.
 * A comment is prose, so comments are stripped first.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve, relative } from 'node:path';
import { stripComments } from '../helpers/stripComments.js';

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC_DIR = resolve(HERE, '../../src');
const LOCALES_DIR = join(SRC_DIR, 'locales');
const EXCLUDED_DIRS = [join(SRC_DIR, 'locales'), join(SRC_DIR, 'components/gallery')];

// Units as the eight locales spelled them, the scripts' own included. Hindi's
// second is its CLDR abbreviation, से॰: bare, से is the postposition "from".
const LOCALE_UNIT = String.raw`(?:s|sec|seg|Sek\.?|ms|min|Min\.?|h|dB|Hz|kHz|MB|GB|Mo|Go|°C|%|秒|分钟|से॰)`;
const LOCALE_UNIT_AFTER_VALUE = new RegExp(String.raw`(?:\{\w+\}|\d)[\s  ]?${LOCALE_UNIT}(?![\p{L}\p{M}])`, 'u');

// `{{ x }} dB` in a template.
const TEMPLATE_UNIT = /\}\}[\s ]?(?:dB|Hz|kHz|ms|s|sec|min|h|%|°C|MB|GB|kB\/s|KB\/s|kbps)(?=[\s<'"]|$)/m;
// `${x} kbps` in script: no unit CSS also uses (%, ms, s, px), those are styles there.
const SCRIPT_UNIT = /\$\{[^}]*\}[\s ]?(?:dB|Hz|kHz|sec|min|°C|MB|GB|kB\/s|KB\/s|kbps)(?![\p{L}])/u;

function localeStrings() {
  const strings = [];
  const walk = (node, path, file) => {
    for (const [key, value] of Object.entries(node)) {
      if (typeof value === 'string') strings.push({ where: `${file} ${path}${key}`, value });
      else walk(value, `${path}${key}.`, file);
    }
  };
  for (const file of readdirSync(LOCALES_DIR).filter(f => f.endsWith('.json'))) {
    walk(JSON.parse(readFileSync(join(LOCALES_DIR, file), 'utf8')), '', file);
  }
  return strings;
}

function sourceFiles(dir = SRC_DIR) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return EXCLUDED_DIRS.includes(path) ? [] : sourceFiles(path);
    return /\.(vue|js)$/.test(name) ? [path] : [];
  });
}

function code(path) {
  return stripComments(readFileSync(path, 'utf8').replace(/<style[\s\S]*?<\/style>/g, ''));
}

describe('units in locale strings', () => {
  const strings = localeStrings();

  it('reads every string of every locale', () => {
    expect(strings.length).toBeGreaterThan(8 * 500);
  });

  it('the pattern catches the shapes it exists for', () => {
    for (const drift of ['{seconds} s left', '{seconds}s remaining', 'Il reste {seconds} s', '(85 °C)', 'Maximum 5 MB.', '剩余 {seconds} 秒', '{seconds} से॰ शेष']) {
      expect(drift).toMatch(LOCALE_UNIT_AFTER_VALUE);
    }
    // A word that merely begins like a unit is not one.
    expect('{count} songs').not.toMatch(LOCALE_UNIT_AFTER_VALUE);
    expect('{count} minutes').not.toMatch(LOCALE_UNIT_AFTER_VALUE);
    expect('{ssid} से कनेक्ट करें').not.toMatch(LOCALE_UNIT_AFTER_VALUE);
  });

  it('no string writes a unit after a value — the value arrives formatted', () => {
    const offenders = strings.filter(({ value }) => LOCALE_UNIT_AFTER_VALUE.test(value)).map(({ where, value }) => `${where}: ${value}`);
    expect(offenders).toEqual([]);
  });
});

describe('units in components and composables', () => {
  const files = sourceFiles();

  it('reads the whole UI, the gallery aside', () => {
    expect(files.length).toBeGreaterThan(150);
    expect(files.some(f => f.includes('/gallery/'))).toBe(false);
  });

  it('the patterns catch the shapes they exist for', () => {
    expect('<span>{{ measured.rtt_max_ms }} ms</span>').toMatch(TEMPLATE_UNIT);
    expect('<span>{{ cpuPercent }}%</span>').toMatch(TEMPLATE_UNIT);
    expect('const bitrate = `${station.bitrate} kbps`;').toMatch(SCRIPT_UNIT);
    expect('return `${h}h ${m}min`;').toMatch(SCRIPT_UNIT);
    // A duration in a style is CSS, not text.
    expect('{ \'--step-ms\': `${stepMs}ms` }').not.toMatch(SCRIPT_UNIT);
  });

  it('no interpolation is followed by a hand-written unit', () => {
    const offenders = files.flatMap((path) => {
      const source = code(path);
      return [TEMPLATE_UNIT, SCRIPT_UNIT]
        .map(pattern => source.match(pattern)?.[0])
        .filter(Boolean)
        .map(match => `${relative(SRC_DIR, path)}: ${match}`);
    });
    expect(offenders).toEqual([]);
  });
});
