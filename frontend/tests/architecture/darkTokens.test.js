// frontend/tests/architecture/darkTokens.test.js
/**
 * The dark theme is a second block of design-system.css,
 * `:root[data-theme="dark"]`, that restates every themable token of `:root`.
 *
 * Two drifts break it, both silently. A themable token added to `:root` with no
 * dark twin keeps its light value in the dark theme — a white surface or a
 * near-black ink on a dark ground, found only by eye on the one screen that uses
 * it. And a name in the dark block that `:root` does not declare is a dead
 * value: a typo, or a token renamed on one side, which leaves the real one
 * untouched in the dark theme while the block reads as if it were handled.
 *
 * Themable means `--color-*`, `--gradient-*` and `--stroke-*`. The other
 * families — the `--gray-*` palette, spacing, radii, type, motion, shadows —
 * are theme-neutral by design and are not read here. A themable token that is
 * neutral on purpose has to be named in NEUTRAL, and that list is checked too,
 * so an entry cannot outlive its token or start being themed behind the
 * list's back.
 *
 * Mounts nothing: it reads the stylesheet the browser reads.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const DESIGN_SYSTEM = resolve(HERE, '../../src/assets/styles/design-system.css');

const THEMABLE = /^--(?:color|gradient|stroke)-/;

/**
 * Themable by name, theme-neutral on purpose. The brand and the status colors
 * mean the same thing in both themes; glass is tinted by what is behind it;
 * a contrast surface is dark in both themes, so what is drawn on it is too;
 * and artwork does not change with the theme, so neither does what is drawn
 * over it.
 */
const NEUTRAL = [
  '--color-brand', '--color-text-on-brand', '--color-brand-subtle',
  '--color-success', '--color-warning', '--color-error',
  '--color-success-subtle', '--color-warning-subtle', '--color-error-subtle',
  '--color-glass', '--color-glass-strong',
  '--color-glint', '--color-text-on-contrast', '--color-text-on-contrast-secondary',
  '--color-shell-on-contrast',
  '--color-image-plate', '--color-image-veil', '--color-image-scrim',
  '--color-backdrop', '--color-backdrop-veil',
];

/** The declarations of the first rule whose selector is exactly `selector`. */
function block(css, selector) {
  const opener = new RegExp(`^${selector.replace(/[[\]"=]/g, '\\$&')}\\s*\\{`, 'm');
  const match = opener.exec(css);
  if (!match) return null;
  let depth = 1;
  let i = match.index + match[0].length;
  const start = i;
  while (depth > 0 && i < css.length) {
    if (css[i] === '{') depth++;
    if (css[i] === '}') depth--;
    i++;
  }
  return css.slice(start, i - 1);
}

/** The custom properties a block declares, comments dropped. */
function declared(body) {
  const code = body.replace(/\/\*[\s\S]*?\*\//g, '');
  return new Set([...code.matchAll(/(--[\w-]+)\s*:/g)].map((m) => m[1]));
}

const css = readFileSync(DESIGN_SYSTEM, 'utf8');
const light = declared(block(css, ':root') ?? '');
const dark = declared(block(css, ':root[data-theme="dark"]') ?? '');
const themable = [...light].filter((name) => THEMABLE.test(name));

describe('dark theme tokens', () => {
  it('reads both blocks', () => {
    // A selector that stopped matching would leave both checks below
    // comparing empty sets, and passing.
    expect(light.size).toBeGreaterThan(50);
    expect(themable.length).toBeGreaterThan(20);
    expect(dark.size).toBeGreaterThan(20);
  });

  it('gives every themable token of :root a dark twin', () => {
    const missing = themable.filter((name) => !NEUTRAL.includes(name) && !dark.has(name));
    expect(missing).toEqual([]);
  });

  it('redefines nothing in the dark block that :root does not declare', () => {
    const unknown = [...dark].filter((name) => !light.has(name));
    expect(unknown).toEqual([]);
  });

  it('keeps every neutral exception a live, unthemed token', () => {
    const stale = NEUTRAL.filter((name) => !light.has(name) || dark.has(name));
    expect(stale).toEqual([]);
  });
});
