// frontend/tests/pure/podcastGenres.test.js
/**
 * The podcast genres the browse grid draws: the backend serves the keys
 * (GET /api/podcast/genres, `GENRE_TO_ITUNES_ID` in
 * backend/sources/podcast/podcast_catalog.py), the frontend ships an artwork
 * and a label for each, looked up by key.
 *
 * Nothing at runtime notices when the two sides stop agreeing: a key with no
 * file renders a tile with no image, a key with no label shows the raw i18n
 * path, and a key the backend no longer maps opens on "no podcast in this
 * genre". So this reads the backend's table and asserts the join: rename a key
 * on one side only, or add a genre without its artwork or label, and it goes red.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { genreSlug, genreArtwork } from '@/constants/podcastGenres';
import english from '@/locales/english.json';

const HERE = dirname(fileURLToPath(import.meta.url));
const CATALOG = resolve(HERE, '../../../backend/sources/podcast/podcast_catalog.py');

/** The backend's `GENRE_TO_ITUNES_ID = { ... }` literal: its entry lines, and the keys read from them. */
function backendTable() {
  const source = readFileSync(CATALOG, 'utf8');
  const table = source.match(/^GENRE_TO_ITUNES_ID = \{\n([\s\S]*?)^\}/m);
  if (!table) return { lines: [], genres: [] };
  const lines = table[1].split('\n').map(line => line.trim()).filter(line => line && !line.startsWith('#'));
  const genres = lines.map(line => /^['"]([A-Z_]+)['"]\s*:/.exec(line)?.[1]).filter(Boolean);
  return { lines, genres };
}

describe('podcast genres', () => {
  const { lines, genres } = backendTable();

  it('reads the backend table', () => {
    // A parse that found nothing would make every check below vacuous, and one
    // that skipped a line (another quoting, no trailing comma) would let that
    // key escape them: every entry line must yield its key.
    expect(genres.length).toBeGreaterThan(5);
    expect(genres.length).toBe(lines.length);
  });

  it('ships an artwork for every genre the backend serves', () => {
    expect(genres.filter(id => !genreArtwork(id))).toEqual([]);
  });

  it('labels every genre the backend serves', () => {
    expect(genres.filter(id => !english.podcasts.genres[genreSlug(id)])).toEqual([]);
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
