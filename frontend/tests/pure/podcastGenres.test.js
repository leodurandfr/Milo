// frontend/tests/pure/podcastGenres.test.js
/**
 * The podcast genre vocabulary, read by HomeView's browse grid, GenreCard's
 * artwork and the gallery's `value` select.
 *
 * The list and the artwork are two independent declarations — twelve ids here,
 * twelve `.jpg` in assets/podcasts/genres/ — and nothing at runtime notices when
 * they stop agreeing: an id with no file renders a tile with a broken image and
 * no error. That is exactly how the gallery came to show twelve imageless tiles
 * from a list written in the wrong vocabulary. So this asserts the join, not the
 * list: rename a file, or add a genre without its artwork, and it goes red.
 */
import { describe, it, expect } from 'vitest';
import { PODCAST_GENRE_IDS, genreSlug, genreArtwork } from '@/constants/podcastGenres';

describe('podcast genres', () => {
  it('resolves an artwork for every id it offers', () => {
    // A glob that matched nothing would make the check below vacuous.
    expect(PODCAST_GENRE_IDS.length).toBeGreaterThan(5);

    const missing = PODCAST_GENRE_IDS.filter(id => !genreArtwork(id));

    expect(missing).toEqual([]);
  });

  it('answers nothing for an id no file backs', () => {
    // The lookup must fail rather than fall back: a wrong id has to be visible.
    expect(genreArtwork('PODCASTSERIES_NOT_A_GENRE')).toBeUndefined();
    // And the label vocabulary is not the API's — the gallery once offered it.
    expect(genreArtwork('comedy')).toBeUndefined();
  });

  it('slugs an id into the name its file and its i18n key share', () => {
    expect(genreSlug('PODCASTSERIES_TRUE_CRIME')).toBe('true_crime');
  });
});
