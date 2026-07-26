// frontend/tests/i18n/locales.test.js
/**
 * Structural guardrail over the 8 locales.
 *
 * english.json is canonical (it is also the runtime fallback), so every other
 * locale must carry exactly its key set — no missing key silently falling back,
 * no orphan key surviving a rename.
 *
 * Mounts nothing, reads no markup: this only goes red when the translation
 * surface actually diverges from the code.
 */
import { describe, it, expect } from 'vitest';
import { scanI18nUsage, loadLocales } from '../helpers/i18nScan';

const locales = loadLocales();
const CANONICAL = 'english';
const englishKeys = Object.keys(locales[CANONICAL]);
const otherLocales = Object.keys(locales).filter(name => name !== CANONICAL);

const usage = scanI18nUsage();

/** A key is live if referenced literally or covered by a dynamic prefix. */
function isReferenced(key) {
  if (usage.referencedKeys.has(key)) return true;
  for (const prefix of usage.dynamicPrefixes) {
    if (key.startsWith(prefix)) return true;
  }
  return false;
}

describe('locale inventory', () => {
  it('ships the 8 supported languages', () => {
    expect(Object.keys(locales).sort()).toEqual([
      'chinese', 'english', 'french', 'german',
      'hindi', 'italian', 'portuguese', 'spanish',
    ]);
  });

  it('canonical english.json is non-trivial', () => {
    // Guards the loader: an empty parse would make every check below vacuous.
    expect(englishKeys.length).toBeGreaterThan(100);
  });

  describe.each(otherLocales)('%s', (name) => {
    const keys = new Set(Object.keys(locales[name]));

    it('has no key missing against english.json', () => {
      const missing = englishKeys.filter(key => !keys.has(key));

      // A missing key silently renders the English text instead.
      expect(missing).toEqual([]);
    });

    it('has no key english.json does not declare', () => {
      const orphans = [...keys].filter(key => !(key in locales[CANONICAL]));

      // An orphan key is dead weight left behind by a rename.
      expect(orphans).toEqual([]);
    });

    it('keeps the same interpolation placeholders as english.json', () => {
      const placeholders = (value) => [...value.matchAll(/\{(\w+)\}/g)].map(m => m[1]).sort();
      const mismatched = [];

      for (const key of englishKeys) {
        if (!keys.has(key)) continue;
        const expected = placeholders(locales[CANONICAL][key]);
        const actual = placeholders(locales[name][key]);
        if (expected.join(',') !== actual.join(',')) {
          mismatched.push(`${key}: expected {${expected}}, got {${actual}}`);
        }
      }

      // A dropped {n} renders a sentence with a hole in it.
      expect(mismatched).toEqual([]);
    });
  });

  describe.each(Object.keys(locales))('%s values', (name) => {
    it('are non-empty strings', () => {
      const bad = Object.entries(locales[name])
        .filter(([, value]) => typeof value !== 'string' || value.trim() === '')
        .map(([key]) => key);

      expect(bad).toEqual([]);
    });
  });
});

describe('code ↔ english.json', () => {
  it('scanned a representative part of the source tree', () => {
    // Guards the scanner: a broken walk would make both checks below vacuous.
    expect(usage.fileCount).toBeGreaterThan(50);
    expect(usage.calledKeys.size).toBeGreaterThan(100);
  });

  it('every key passed to t() exists in english.json', () => {
    const known = new Set(englishKeys);
    const missing = [...usage.calledKeys]
      .filter(([key]) => !known.has(key))
      .map(([key, file]) => `${key} (${file})`);

    // A key absent from the canonical locale renders as the raw key path.
    expect(missing).toEqual([]);
  });

  it('every key in english.json is referenced somewhere', () => {
    // Indirect references count: lookup maps (SetupWizard's stepTitles,
    // AudioStep's categoryLabelMap, multiroomStore's MULTIROOM_ERROR_KEYS) and
    // dynamic prefixes (`radio.genres.${key}`) — see helpers/i18nScan.js.
    const unused = englishKeys.filter(key => !isReferenced(key));

    expect(unused).toEqual([]);
  });

  it('still resolves the known dynamic prefixes', () => {
    // A subset check, not equality: a new `t(`foo.${x}`)` is a normal change and
    // must not redden CI. What matters is that the scanner keeps seeing the
    // template forms it already handles — losing one would make the unused-key
    // check above start reporting live keys as dead.
    expect([...usage.dynamicPrefixes].sort()).toEqual(expect.arrayContaining([
      'equalizer.presets.',
      'multiroom.systemTypes.',
      'multiroomSettings.',
      'radio.genres.',
    ]));
  });
});
