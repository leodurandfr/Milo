// frontend/tests/i18n/languageList.test.js
/**
 * Guardrail over the one list of supported languages, which is spelled five
 * times across both halves of the app.
 *
 * The failure it exists for is silent: a language the backend accepts but whose
 * branch is missing from `loadTranslations` leaves `translations` undefined, so
 * nothing is stored and `t()` answers from the English fallback for the *whole*
 * interface — with no log at all, because the `catch` only fires on an import
 * that throws, never on a branch that was not taken. The other three spellings
 * fail just as quietly: `bcp47For` returns 'en', the picker hides the language,
 * and the locale file is dead weight.
 *
 * Reads the backend enum as text, the way `schemas/api.test.js` reads
 * `audio_state.py` — nothing here is bundled.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync } from 'fs';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';

const HERE = dirname(fileURLToPath(import.meta.url));
const I18N_PATH = resolve(HERE, '../../src/services/i18n.js');
const COUNTRIES_PATH = resolve(HERE, '../../src/constants/countries.js');
const BACKEND_PATH = resolve(HERE, '../../../backend/api/models.py');
const LOCALES_DIR = resolve(HERE, '../../src/locales');

const i18nSource = readFileSync(I18N_PATH, 'utf8');

/** The `Literal[...]` the backend validates `PUT /api/settings/language` against. */
function backendLanguages() {
  const line = readFileSync(BACKEND_PATH, 'utf8').match(/SUPPORTED_LANGUAGES\s*=\s*Literal\[([^\]]+)\]/);
  return line ? [...line[1].matchAll(/'([a-z]+)'/g)].map(m => m[1]).sort() : [];
}

/** The `else if (language === 'x')` chain that actually imports a locale file. */
function loadableLanguages() {
  const body = i18nSource.match(/async loadTranslations\(language\)\s*\{[\s\S]*?\n {2}\}/);
  return body ? [...body[0].matchAll(/language === '([a-z]+)'/g)].map(m => m[1]).sort() : [];
}

/** What the language picker offers. */
function offeredLanguages() {
  const body = i18nSource.match(/getAvailableLanguages\(\)\s*\{[\s\S]*?\n {2}\}/);
  return body ? [...body[0].matchAll(/code: '([a-z]+)'/g)].map(m => m[1]).sort() : [];
}

/** The BCP-47 tags `Intl.DisplayNames` and `Intl.Collator` are built from. */
function taggedLanguages() {
  const body = readFileSync(COUNTRIES_PATH, 'utf8').match(/LANGUAGE_TO_BCP47\s*=\s*\{([^}]+)\}/);
  return body ? [...body[1].matchAll(/(\w+):\s*'[\w-]+'/g)].map(m => m[1]).sort() : [];
}

const shipped = readdirSync(LOCALES_DIR).filter(f => f.endsWith('.json')).map(f => f.replace('.json', '')).sort();

describe('supported languages', () => {
  it('every extractor found a non-trivial list', () => {
    // Guards the four regexes: one that silently matched nothing would make
    // every comparison below vacuous, and the whole file would pass on rubble.
    for (const [name, list] of [
      ['backend', backendLanguages()],
      ['loadTranslations', loadableLanguages()],
      ['getAvailableLanguages', offeredLanguages()],
      ['LANGUAGE_TO_BCP47', taggedLanguages()],
      ['locale files', shipped],
    ]) {
      expect(list.length, `${name} extracted nothing`).toBeGreaterThan(5);
    }
  });

  it.each([
    ['the backend accepts', backendLanguages],
    ['loadTranslations can import', loadableLanguages],
    ['the picker offers', offeredLanguages],
    ['LANGUAGE_TO_BCP47 tags', taggedLanguages],
  ])('%s exactly the languages shipped as locale files', (_label, extract) => {
    expect(extract()).toEqual(shipped);
  });
});
