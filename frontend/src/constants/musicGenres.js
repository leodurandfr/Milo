/**
 * Genre labels for the Radio source.
 *
 * The list itself is the backend's (`GET /api/radio/genres`, the set
 * `sources/radio/genres.py` matches station tags against), fetched by the radio
 * store. Values are sent to Radio Browser as the `tag` query parameter
 * (substring match on station tags).
 *
 * Most genre names are language-invariant. Only the few entries listed in
 * `radio.genres.*` translation keys differ across locales; everything else
 * falls back to first-letter capitalization.
 */
import { bcp47For } from '@/constants/countries';
import { i18n } from '@/services/i18n';

function genreI18nKey(genre) {
  return genre.replace(/[\s&]+/g, '_').replace(/-/g, '_').toLowerCase();
}

/**
 * Translate a canonical genre key into the UI language.
 *
 * Looks up `radio.genres.<key>` in i18n where `<key>` is the genre normalized
 * (spaces/hyphens/`&` → `_`, lowercased). Falls back to capitalize-first-letter
 * on the raw English genre when no translation key exists.
 *
 * @param {string} language - Milō language code (unused at lookup time — the
 *   i18n singleton already knows the current language — but kept in the
 *   signature so callers stay reactive when the language changes).
 * @param {string} genre - Canonical genre slug (e.g. 'hip-hop', 'r&b').
 * @returns {string}
 */
export function getTranslatedGenreName(language, genre) {
  if (!genre) return '';

  const key = genreI18nKey(genre);
  const path = `radio.genres.${key}`;
  const translated = i18n.t(path);
  if (translated && translated !== path) {
    return translated;
  }

  return genre.charAt(0).toUpperCase() + genre.slice(1);
}

/**
 * Build dropdown options for the genre filter.
 *
 * Options are translated via `getTranslatedGenreName` and sorted alphabetically
 * using the UI language's collation.
 *
 * @param {string} language - Milō language code
 * @param {string[]} genres - The backend's genre list (empty until it arrives)
 * @param {string} allGenresLabel - Label for the "All genres" option
 * @returns {Array<{label: string, value: string}>}
 */
export function genreOptions(language, genres, allGenresLabel) {
  const bcp47 = bcp47For(language);

  const translated = genres.map((g) => ({
    label: getTranslatedGenreName(language, g),
    value: g,
  }));

  translated.sort((a, b) => a.label.localeCompare(b.label, bcp47));

  return [{ label: allGenresLabel, value: '' }, ...translated];
}
