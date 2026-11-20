// frontend/src/constants/podcastGenres.js
/**
 * A podcast genre key's slug and artwork. The genres themselves are the
 * backend's (GET /api/podcast/genres, `GENRE_TO_ITUNES_ID` in
 * sources/podcast/podcast_catalog.py), fetched by the podcast store; nothing
 * here restates that list. tests/pure/podcastGenres.test.js reads the backend
 * table and holds every key it serves to an artwork and a label here.
 *
 * The artwork is matched by file name rather than by a second map: a genre's
 * slug is its file in `assets/podcasts/genres/`, which is one fact to keep true
 * instead of two. Same arrangement as AppIcon's glob over `app-icons/`.
 */
const artworkModules = import.meta.glob('@/assets/podcasts/genres/*.jpg', {
  eager: true,
  import: 'default'
});

const PREFIX = 'PODCASTSERIES_';

/** `PODCASTSERIES_TRUE_CRIME` → `true_crime`: both the i18n key and the file name. */
export function genreSlug(id) {
  return id.replace(PREFIX, '').toLowerCase();
}

/** The files themselves, keyed by the name they carry on disk. */
const FILES = Object.fromEntries(
  Object.entries(artworkModules).map(([path, url]) => [path.match(/\/([^/]+)\.jpg$/)[1], url])
);

/**
 * The genre keys an artwork ships for — what the gallery offers without a
 * backend. Derived from the files, never written out.
 */
export const PODCAST_GENRE_IDS = Object.keys(FILES).map(slug => PREFIX + slug.toUpperCase());

/**
 * The tile artwork for a genre key, or undefined when no file ships for it.
 * Only a key answers: a slug lookup would answer for `comedy` as happily as for
 * `PODCASTSERIES_COMEDY` — the file is named after the slug — so the label
 * vocabulary that once broke the gallery would have kept on working, silently.
 */
export function genreArtwork(id) {
  return id.startsWith(PREFIX) ? FILES[genreSlug(id)] : undefined;
}
