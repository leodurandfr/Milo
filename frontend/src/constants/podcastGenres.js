// frontend/src/constants/podcastGenres.js
/**
 * The genre ids the Podcast Index answers to, in the order the browse grid
 * shows them.
 *
 * One declaration for three readers: HomeView builds the grid from it,
 * GenreCard resolves its artwork through it, and the gallery offers it as a
 * select. It was restated per reader before, and the third copy was written
 * from the labels — `comedy` where the API says `PODCASTSERIES_COMEDY` — so
 * every tile on the gallery page rendered with no artwork at all, including the
 * default, and nothing anywhere disagreed.
 *
 * The artwork is matched by file name rather than by a second map: a genre's
 * slug is its file in `assets/podcasts/genres/`, which is one fact to keep true
 * instead of two. Same arrangement as AppIcon's glob over `app-icons/`.
 */
const artworkModules = import.meta.glob('@/assets/podcasts/genres/*.jpg', {
  eager: true,
  import: 'default'
});

export const PODCAST_GENRE_IDS = [
  'PODCASTSERIES_COMEDY',
  'PODCASTSERIES_SOCIETY_AND_CULTURE',
  'PODCASTSERIES_NEWS',
  'PODCASTSERIES_TRUE_CRIME',
  'PODCASTSERIES_BUSINESS',
  'PODCASTSERIES_EDUCATION',
  'PODCASTSERIES_HEALTH_AND_FITNESS',
  'PODCASTSERIES_SPORTS',
  'PODCASTSERIES_ARTS',
  'PODCASTSERIES_SCIENCE',
  'PODCASTSERIES_TV_AND_FILM',
  'PODCASTSERIES_MUSIC'
];

/** `PODCASTSERIES_TRUE_CRIME` → `true_crime`: both the i18n key and the file name. */
export function genreSlug(id) {
  return id.replace('PODCASTSERIES_', '').toLowerCase();
}

/** The files themselves, keyed by the name they carry on disk. */
const FILES = Object.fromEntries(
  Object.entries(artworkModules).map(([path, url]) => [path.match(/\/([^/]+)\.jpg$/)[1], url])
);

/**
 * Keyed by the id, not by its slug. A slug lookup would answer for `comedy` as
 * happily as for `PODCASTSERIES_COMEDY` — the file is named after the slug — so
 * the vocabulary that broke the gallery would have kept on working, silently.
 */
const ARTWORK = Object.fromEntries(PODCAST_GENRE_IDS.map(id => [id, FILES[genreSlug(id)]]));

/** The tile artwork for a genre id, or undefined when no file ships for it. */
export function genreArtwork(id) {
  return ARTWORK[id];
}
