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

/** The `{n}` names a string interpolates, sorted. Non-strings have none. */
function placeholdersOf(value) {
  if (typeof value !== 'string') return [];
  return [...value.matchAll(/\{(\w+)\}/g)].map(m => m[1]).sort();
}

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
      const mismatched = [];

      for (const key of englishKeys) {
        if (!keys.has(key)) continue;
        const expected = placeholdersOf(locales[CANONICAL][key]);
        const actual = placeholdersOf(locales[name][key]);
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

  it('fills every {placeholder} the called string declares', () => {
    // A string that interpolates and a call that passes nothing renders the
    // braces: "{name} needs re-indexing", on any mounted-but-unindexed space,
    // from a clean prod boot. Both halves of that bug lived on adjacent lines —
    // the params were passed to the neighbouring key, which has no placeholder.
    const unfilled = [...usage.paramlessCalls]
      .filter(([key]) => placeholdersOf(locales[CANONICAL][key]).length)
      .map(([key, file]) => `${key} (${file})`);

    expect(unfilled).toEqual([]);
  });

  it('passes params only to strings that interpolate', () => {
    // The mirror half: params handed to a string with no placeholder do nothing
    // at all, so they read as filled while the real hole sits elsewhere.
    const wasted = [...usage.parameterisedCalls]
      .filter(([key]) => key in locales[CANONICAL] && !placeholdersOf(locales[CANONICAL][key]).length)
      .map(([key, file]) => `${key} (${file})`);

    expect(wasted).toEqual([]);
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

/**
 * One English string under several keys must translate the same way in every
 * locale — unless the keys mean different things, in which case the pair is
 * listed below with the reason.
 *
 * This is the content half of the surface the checks above guard structurally.
 * The structural ones cannot see it: each locale carries the right keys with
 * the right placeholders while saying "Redémarrage…" in one place and
 * "Redémarrage en cours…" in another. Measured before this rule existed, 25 of
 * the 56 duplicated English strings diverged somewhere, across 7 of the 8
 * locales — Spanish and Portuguese were the only consistent ones, so this is
 * not a French problem, it is a missing rule.
 *
 * Keyed by the English string. The value is why divergence is correct there;
 * an entry with no real reason is a bug in this list, not a licence.
 */
const DIVERGENCE_ALLOWED = {
  // Three keys exist precisely so French can agree the article with the source
  // noun: "Démarrage de" / "du" / "de la".
  'Starting': 'French gender agreement — that is what the three keys are for',

  // The four subjects are not the same kind of thing: a network, a USB stick
  // (branché, not connecté), and two remotes that are feminine in French.
  'Connected': 'subject differs in gender and in kind (a USB stick is plugged, not connected)',
  'Not connected': 'subject differs in gender and in kind (see Connected)',

  // An example share name shown in a placeholder, not the word "music".
  'Music': 'musicLibrary.shares.sharePlaceholder is an example share name, not the noun',

  // The BT remote is a rotary encoder (par cran), the IR remote is a button
  // (par appui). The translations are more precise than the English here.
  'Volume step per click': 'BT remote is a rotary, IR remote is a button — the locales say which',

  // A search placeholder ("Nom d'une station") against a field label
  // ("Nom de la station").
  'Station name': 'a placeholder against a field label',

  // The EQ preset catalogue is its own naming family (Amplificateur de basses /
  // Réducteur de basses / Amplificateur vocal…). A preset that happens to share
  // an English label with a loudness control is not that control, and renaming
  // it would break the family.
  'Loudness': 'the EQ preset catalogue is a naming family of its own',
  'Bass boost': 'the EQ preset catalogue is a naming family of its own',
  'Treble boost': 'the EQ preset catalogue is a naming family of its own',
  'Electronic': 'EQ preset catalogue vs the radio-browser genre taxonomy',
};

describe('translation consistency', () => {
  const byEnglishString = new Map();
  for (const key of englishKeys) {
    const value = locales[CANONICAL][key];
    if (typeof value !== 'string' || value.length <= 2) continue;
    if (!byEnglishString.has(value)) byEnglishString.set(value, []);
    byEnglishString.get(value).push(key);
  }
  const duplicated = [...byEnglishString].filter(([, keys]) => keys.length > 1);

  it('found duplicated english strings to check', () => {
    // Guards the grouping: an empty map would make the check below vacuous.
    expect(duplicated.length).toBeGreaterThan(20);
  });

  it('translates one english string the same way in each locale', () => {
    const diverging = [];

    for (const [english, keys] of duplicated) {
      if (english in DIVERGENCE_ALLOWED) continue;
      for (const name of otherLocales) {
        const values = new Set(keys.map(key => locales[name][key]));
        if (values.size > 1) {
          diverging.push(`${name}: ${JSON.stringify(english)} -> ${[...values].map(v => JSON.stringify(v)).join(' | ')}`);
        }
      }
    }

    // Two wordings for one concept, shipped side by side in the same UI.
    expect(diverging).toEqual([]);
  });

  it('allows divergence only for strings that are actually duplicated', () => {
    // An entry left behind by a rename silently exempts nothing, and would hide
    // the next real divergence if the string came back.
    const stale = Object.keys(DIVERGENCE_ALLOWED)
      .filter(english => !byEnglishString.get(english) || byEnglishString.get(english).length < 2);

    expect(stale).toEqual([]);
  });
});

