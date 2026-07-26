// frontend/tests/pure/keyboardLayouts.test.js
/**
 * Structural guardrail over the virtual keyboard's key table.
 *
 * The row lengths are dictated by the CSS grid in VirtualKeyboard.vue — rows 1
 * and 2 fill `repeat(10, 1fr)`, row 3 fills 8 because [caps] and [enter] take
 * the first and last column. A row of the wrong length does not throw: it draws
 * a keyboard with empty columns, or keys that overflow their cell, and only on
 * the Pi's own touchscreen where the keyboard is the sole text-entry route.
 *
 * That makes this the test to read before editing the table — in particular
 * before removing the stray `ō` / `・` / `⸱` from the punctuation row, which
 * needs three *replacement* glyphs rather than three deletions.
 *
 * The locale list comes from src/locales/, so a language added there without a
 * layout shows up as an explicit fallback rather than as a silent one.
 */
import { describe, it, expect } from 'vitest';
import { keyLayout, accentVariants } from '@/components/ui/keyboard/layouts';
import { loadLocales } from '../helpers/i18nScan';

const LANGUAGES = Object.keys(loadLocales());
const MODES = ['abc', 'numbers', 'symbols'];

// Languages with their own layout. chinese and hindi resolve to english on
// purpose — a latin QWERTY cannot type either, they need an IME.
const LAID_OUT = ['english', 'french', 'german', 'italian', 'portuguese', 'spanish'];

const allKeys = (lang) => MODES.flatMap((mode) => {
  const rows = keyLayout(lang)[mode];
  return [...rows.row1, ...rows.row2, ...rows.row3];
});

describe('keyboard layout table', () => {
  it('reads a non-trivial locale list', () => {
    // Guards the loader: an empty list would make every check below vacuous.
    expect(LANGUAGES.length).toBeGreaterThanOrEqual(8);
    expect(LANGUAGES).toContain('english');
  });

  it.each(LANGUAGES)('%s fills the CSS grid in all three modes', (lang) => {
    for (const mode of MODES) {
      const rows = keyLayout(lang)[mode];
      expect(rows.row1).toHaveLength(10);
      expect(rows.row2).toHaveLength(10);
      expect(rows.row3).toHaveLength(8);
    }
  });

  it.each(LANGUAGES)('%s uses one character per key', (lang) => {
    for (const key of allKeys(lang)) {
      expect([...key]).toHaveLength(1);
    }
  });

  it.each(LANGUAGES)('%s repeats no key inside a mode', (lang) => {
    for (const mode of MODES) {
      const rows = keyLayout(lang)[mode];
      const keys = [...rows.row1, ...rows.row2, ...rows.row3];
      expect(new Set(keys).size).toBe(keys.length);
    }
  });

  it.each(LANGUAGES)('%s can reach every accent it declares', (lang) => {
    // An accent hangs off a long press, so its base key must exist somewhere —
    // spanish ¿/¡ live on ? and !, which are in the numbers row, not abc.
    const reachable = new Set(allKeys(lang));
    for (const base of Object.keys(accentVariants(lang))) {
      expect(reachable).toContain(base);
    }
  });

  it('falls back to english for a locale with no layout of its own', () => {
    const withoutLayout = LANGUAGES.filter((l) => !LAID_OUT.includes(l));
    expect(withoutLayout.length).toBeGreaterThan(0);
    for (const lang of withoutLayout) {
      expect(keyLayout(lang)).toEqual(keyLayout('english'));
      expect(accentVariants(lang)).toEqual(accentVariants('english'));
    }
  });
});
