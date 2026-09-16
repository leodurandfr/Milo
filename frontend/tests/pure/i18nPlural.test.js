// frontend/tests/pure/i18nPlural.test.js
/**
 * The plural selector and the number formatter of `services/i18n.js`.
 *
 * Milō has no i18n library: `t()` is a hand-rolled lookup, so the rule that
 * picks between `{count} track` and `{count} tracks` is Milō's own code and
 * nothing else tests it. Before it existed every counted string was written in
 * the plural and an artist with one album read "1 albums", a library of ten
 * thousand tracks read "10069 titres".
 *
 * Goes red if a language's rule changes, or if a count stops being grouped.
 */
import { describe, it, expect, afterEach } from 'vitest';
import { i18n } from '@/services/i18n';

const LOCALES = ['english', 'french', 'german', 'spanish', 'italian', 'portuguese', 'chinese', 'hindi'];
const original = i18n.currentLanguage.value;
afterEach(() => { i18n.currentLanguage.value = original; });

function pick(language, template, count) {
  i18n.currentLanguage.value = language;
  return i18n.selectPlural(template, { count });
}

describe('plural selection', () => {
  const TEMPLATE = 'one | many';

  it.each([
    ['english', 1, 'one'], ['english', 0, 'many'], ['english', 2, 'many'],
    // French and Hindi put zero in the singular: "0 morceau", not "0 morceaux".
    ['french', 0, 'one'], ['french', 1, 'one'], ['french', 2, 'many'],
    ['hindi', 0, 'one'], ['hindi', 1, 'one'], ['hindi', 5, 'many'],
    ['german', 1, 'one'], ['german', 0, 'many'],
    ['spanish', 1, 'one'], ['italian', 1, 'one'], ['portuguese', 1, 'one'],
    // Chinese does not inflect: the same form whatever the count.
    ['chinese', 1, 'many'], ['chinese', 0, 'many'], ['chinese', 9, 'many'],
  ])('%s picks the right form for %i', (language, count, expected) => {
    expect(pick(language, TEMPLATE, count)).toBe(expected);
  });

  it('returns a single-form string untouched, in every locale', () => {
    // How Chinese and Hindi keep one form for a string English splits in two.
    for (const language of LOCALES) {
      expect(pick(language, '{count} 曲目', 3)).toBe('{count} 曲目');
    }
  });

  it('leaves a string alone when no count is passed', () => {
    i18n.currentLanguage.value = 'english';
    expect(i18n.selectPlural('one | many', {})).toBe('one | many');
    expect(i18n.selectPlural('one | many', { name: 'x' })).toBe('one | many');
  });
});

describe('number formatting', () => {
  it('groups a count the way the language groups thousands', () => {
    i18n.currentLanguage.value = 'english';
    expect(i18n.interpolate('{count} tracks', { count: 10069 })).toBe('10,069 tracks');

    i18n.currentLanguage.value = 'french';
    // French groups with a narrow no-break space, not a comma.
    expect(i18n.interpolate('{count} morceaux', { count: 10069 })).not.toContain('10069');
    expect(i18n.interpolate('{count} morceaux', { count: 10069 })).toMatch(/^10\s069 morceaux$/u);

    i18n.currentLanguage.value = 'hindi';
    // Indian grouping: 10,069 here, but 1,00,069 one digit further up.
    expect(i18n.interpolate('{count}', { count: 100069 })).toBe('1,00,069');
  });

  it('leaves non-numeric params exactly as given', () => {
    i18n.currentLanguage.value = 'french';
    expect(i18n.interpolate('{name} · {ip}', { name: 'Salon', ip: '192.168.1.10' }))
      .toBe('Salon · 192.168.1.10');
  });

  it('leaves an unfilled placeholder in place', () => {
    // t() renders the braces rather than an empty hole — that is what makes a
    // missing param visible instead of silent.
    expect(i18n.interpolate('{count} tracks', { other: 1 })).toBe('{count} tracks');
  });
});
