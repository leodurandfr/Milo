// frontend/tests/pure/formatList.test.js
/**
 * Names listed the way each language lists them (the Spotify Jam's label):
 * the conjunction and its punctuation are CLDR's, never a join written here.
 * Goes red if formatList stops following the UI language.
 */
import { describe, it, expect } from 'vitest';
import { formatList } from '@/utils/units';

describe('formatList', () => {
  it.each([
    ['english', ['Léo', 'Cla'], 'Léo and Cla'],
    ['english', ['Léo', 'Max', 'Cla'], 'Léo, Max, and Cla'],
    ['french', ['Léo', 'Max', 'Cla'], 'Léo, Max et Cla'],
    ['german', ['Léo', 'Cla'], 'Léo und Cla'],
    ['spanish', ['Léo', 'Cla'], 'Léo y Cla'],
    ['italian', ['Léo', 'Cla'], 'Léo e Cla']
  ])('lists in %s', (language, names, expected) => {
    expect(formatList(names, language)).toBe(expected);
  });

  it('leaves a single name alone', () => {
    expect(formatList(['Léo'], 'french')).toBe('Léo');
  });
});
