/**
 * Country localization for the Radio source.
 *
 * Source of truth: Radio Browser API (`/json/countries?hidebroken=true`),
 * which returns ISO 3166-1 alpha-2 codes. We use `Intl.DisplayNames` to
 * translate those codes into the user's UI language — no manual i18n map.
 *
 * Usage:
 * - API returns: { name: "The United States Of America", iso_3166_1: "US", ... }
 * - Dropdown shows: "United States" (en) / "États-Unis" (fr) / "Estados Unidos" (es) / ...
 * - Filter value: the original API `name` (Radio Browser filters match on it).
 */

/**
 * Maps Milō UI language codes (see services/i18n.js) to BCP-47 tags
 * understood by Intl.DisplayNames and Intl.Collator.
 */
export const LANGUAGE_TO_BCP47 = {
  french: 'fr',
  english: 'en',
  spanish: 'es',
  italian: 'it',
  german: 'de',
  portuguese: 'pt',
  hindi: 'hi',
  chinese: 'zh',
};

/**
 * Country hoisted to the top of the search dropdown based on the user's UI
 * language. `english` is intentionally omitted — too many English-speaking
 * countries to pick a sensible default.
 */
const LANGUAGE_TO_USER_COUNTRY_ISO = {
  french: 'FR',
  spanish: 'ES',
  italian: 'IT',
  german: 'DE',
  portuguese: 'PT',
  hindi: 'IN',
  chinese: 'CN',
};

export function bcp47For(language) {
  return LANGUAGE_TO_BCP47[language] || 'en';
}

/**
 * Translate an ISO 3166-1 alpha-2 country code into the UI language.
 *
 * @param {string} language - Milō language code (e.g. 'french', 'english')
 * @param {string} isoCode - ISO 3166-1 alpha-2 (e.g. 'FR'). Case-insensitive.
 * @param {string} [fallbackName] - Returned if no ISO code or translation fails.
 * @returns {string}
 */
export function getTranslatedCountryName(language, isoCode, fallbackName = '') {
  if (!isoCode) return fallbackName;

  try {
    const display = new Intl.DisplayNames([bcp47For(language)], { type: 'region' });
    const translated = display.of(isoCode.toUpperCase());
    // Intl.DisplayNames returns the input code unchanged when unknown.
    if (translated && translated !== isoCode.toUpperCase()) {
      return translated;
    }
  } catch {
    // Fall through to fallbackName.
  }
  return fallbackName;
}

/**
 * Build dropdown options for the country filter.
 *
 * Options are translated via Intl.DisplayNames and sorted alphabetically
 * using the UI language's collation. The user's locale-derived country (if
 * present in the list) is hoisted to the top — except for English, where
 * there's no sensible default.
 *
 * @param {string} language - Milō language code
 * @param {Array<{name: string, iso_3166_1: string}>} apiCountries
 * @param {string} allCountriesLabel - Label for the "All countries" option
 * @returns {Array<{label: string, value: string}>}
 */
export function countryOptions(language, apiCountries, allCountriesLabel) {
  const bcp47 = bcp47For(language);
  const userIso = LANGUAGE_TO_USER_COUNTRY_ISO[language] || null;

  const translated = apiCountries.map((c) => ({
    label: getTranslatedCountryName(language, c.iso_3166_1, c.name),
    value: c.name,
    iso: (c.iso_3166_1 || '').toUpperCase(),
  }));

  translated.sort((a, b) => a.label.localeCompare(b.label, bcp47));

  if (userIso) {
    const idx = translated.findIndex((c) => c.iso === userIso);
    if (idx > 0) {
      const [hoisted] = translated.splice(idx, 1);
      translated.unshift(hoisted);
    }
  }

  const options = [{ label: allCountriesLabel, value: '', iso: '' }];
  translated.forEach(({ label, value, iso }) => options.push({ label, value, iso }));
  return options;
}
