/**
 * Valid music genres for RadioBrowser API
 *
 * This list is used to:
 * - Filter search results in RadioSource.vue
 * - Validate genre tags from RadioBrowser API (backend)
 *
 * Note: Genres are case-insensitive when matching.
 * The backend normalizes tags to lowercase before comparison.
 */

export const MUSIC_GENRES = [
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
  'hiphop',
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
  'rnb',
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
  'tropical',
  'urban',
  'world music'
];

/**
 * Generate dropdown options from genres
 * @param {Function} t - i18n translation function
 * @param {string} allGenresLabel - Label for "All Genres" option
 * @returns {Array} Array of {label, value} objects for Dropdown component
 */
export function genreOptions(t, allGenresLabel) {
  const options = [{ label: allGenresLabel, value: '' }];

  MUSIC_GENRES.forEach(genre => {
    // Capitalize first letter for display
    const label = genre.charAt(0).toUpperCase() + genre.slice(1);
    options.push({ label, value: genre });
  });

  return options;
}
