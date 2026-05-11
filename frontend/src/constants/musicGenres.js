/**
 * Curated music-genre list for the Radio source.
 *
 * Mirrors `backend/sources/radio/genres.py::VALID_GENRES`. Used to build the
 * SearchView genre filter dropdown. Values are sent to Radio Browser as the
 * `tag` query parameter (substring match on station tags).
 *
 * Most genre names are language-invariant. Only the few entries listed in
 * `radio.genres.*` translation keys differ across locales; everything else
 * falls back to first-letter capitalization.
 */
import { bcp47For } from '@/constants/countries';
import { i18n } from '@/services/i18n';

const MUSIC_GENRES = [
  '60s',
  '70s',
  '80s',
  '90s',
  '1990s',
  '2010s',
  'acoustic',
  'afrobeats',
  'alternative',
  'alternative rock',
  'ambient',
  'americana',
  'art rock',
  'avant-garde',
  'bachata',
  'big band',
  'blues',
  'bluegrass',
  'bossa nova',
  'britpop',
  'celtic',
  'chill',
  'chillout',
  'classic jazz',
  'classic rock',
  'classical',
  'country',
  'dance',
  'dancehall',
  'darkwave',
  'death metal',
  'deep house',
  'disco',
  'downtempo',
  'drum and bass',
  'dub',
  'dubstep',
  'edm',
  'electro',
  'electronic',
  'eurodance',
  'flamenco',
  'folk',
  'folk rock',
  'funk',
  'garage',
  'gospel',
  'groove',
  'grunge',
  'hard rock',
  'hardcore',
  'hip-hop',
  'house',
  'indie',
  'italo disco',
  'jazz',
  'jazz fusion',
  'k-pop',
  'latin',
  'latin music',
  'latin pop',
  'lo-fi',
  'lounge',
  'merengue',
  'metal',
  'minimal',
  'minimal techno',
  'new age',
  'new wave',
  'news',
  'nu disco',
  'oldies',
  'opera',
  'pop',
  'pop dance',
  'pop rock',
  'power metal',
  'progressive house',
  'progressive rock',
  'psychedelic',
  'psychedelic rock',
  'punk',
  'r&b',
  'rap',
  'rare groove',
  'reggae',
  'reggaeton',
  'rock',
  'roots',
  'salsa',
  'schlager',
  'singer-songwriter',
  'ska',
  'smooth jazz',
  'smooth lounge',
  'soul',
  'stoner rock',
  'swing',
  'synthwave',
  'talk',
  'tech house',
  'techno',
  'thrash metal',
  'trance',
  'trap',
  'trip-hop',
  'tropical'
];

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
// eslint-disable-next-line no-unused-vars
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
 * @param {string} allGenresLabel - Label for the "All genres" option
 * @returns {Array<{label: string, value: string}>}
 */
export function genreOptions(language, allGenresLabel) {
  const bcp47 = bcp47For(language);

  const translated = MUSIC_GENRES.map((g) => ({
    label: getTranslatedGenreName(language, g),
    value: g,
  }));

  translated.sort((a, b) => a.label.localeCompare(b.label, bcp47));

  return [{ label: allGenresLabel, value: '' }, ...translated];
}
