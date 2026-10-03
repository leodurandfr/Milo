// frontend/tests/pure/musicGenres.test.js
/**
 * The radio search's genre dropdown, built by RadioSource from the list
 * `GET /api/radio/genres` serves. It must offer exactly that list: a dropdown
 * carrying its own copy is how the frontend comes to offer a genre the backend
 * no longer recognises on a station.
 */
import { describe, it, expect } from 'vitest';
import { genreOptions } from '@/constants/musicGenres';

describe('genreOptions', () => {
  it('offers the genres it is given, after the "all" entry', () => {
    const options = genreOptions('english', ['jazz', 'blues'], 'All genres');

    expect(options.map(o => o.value)).toEqual(['', 'blues', 'jazz']);
    expect(options[0].label).toBe('All genres');
  });

  it('offers only the "all" entry before the list has arrived', () => {
    expect(genreOptions('english', [], 'All genres')).toEqual([{ label: 'All genres', value: '' }]);
  });
});
