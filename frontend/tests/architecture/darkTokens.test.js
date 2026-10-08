// frontend/tests/architecture/darkTokens.test.js
/**
 * The dark theme is not a second block of design-system.css: every themable
 * token of `:root` writes its two values side by side in `light-dark()`, and
 * `:root[data-theme="dark"]` only switches `color-scheme`.
 *
 * Four drifts break it, all silently. A themable token added with one plain
 * value keeps its light value in the dark theme — a white surface or a
 * near-black ink on a dark ground, found only by eye on the one screen that
 * uses it. A `light-dark()` whose two branches are equal is a neutral pretending
 * to be themed. A role restated in the dark block outranks its own
 * `light-dark()`, and with it every `color-scheme: dark` region that relies on
 * it (a modal's header). And a component writing `light-dark()` tests the theme
 * itself, which is the one thing a role exists to spare it.
 *
 * Themable means `--color-*`, `--gradient-*` and `--stroke-*`. The other
 * families — the `--gray-*` palette, spacing, radii, type, motion, shadows —
 * are theme-neutral by design and are not read here. A themable token that is
 * neutral on purpose has to be named in NEUTRAL, and that list is checked too,
 * so an entry cannot outlive its token or start being themed behind the
 * list's back.
 *
 * Mounts nothing: it reads the sources the browser reads.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, relative, resolve } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = resolve(HERE, '../../src');
const DESIGN_SYSTEM = join(SRC, 'assets/styles/design-system.css');

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
  '--color-glass', '--color-glass-strong', '--color-shell-on-contrast',
  '--color-glint', '--color-text-on-contrast', '--color-text-on-contrast-secondary',
  '--color-image-veil', '--color-image-scrim',
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

/** A block's custom properties, comments dropped: name -> value. */
function declarations(body) {
  const code = body.replace(/\/\*[\s\S]*?\*\//g, '');
  return Object.fromEntries([...code.matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)].map((m) => [m[1], m[2].trim()]));
}

/** The two arguments of every `light-dark()` in a value, in order. */
function branches(value) {
  const found = [];
  for (let start = value.indexOf('light-dark('); start !== -1; start = value.indexOf('light-dark(', start + 1)) {
    const open = start + 'light-dark('.length;
    let depth = 0;
    let comma = -1;
    let close = open;
    for (; close < value.length; close++) {
      if (value[close] === '(') depth++;
      else if (value[close] === ')') {
        if (depth === 0) break;
        depth--;
      } else if (value[close] === ',' && depth === 0) comma = close;
    }
    // No top-level comma, or no closing parenthesis, is a parse that failed:
    // both branches come back empty so the sanity check below goes red.
    found.push(comma === -1 || close === value.length
      ? ['', '']
      : [value.slice(open, comma).trim(), value.slice(comma + 1, close).trim()]);
  }
  return found;
}

function sourceFiles(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return sourceFiles(path);
    return /\.(vue|js|css)$/.test(entry.name) ? [path] : [];
  });
}

const css = readFileSync(DESIGN_SYSTEM, 'utf8');
const root = declarations(block(css, ':root') ?? '');
const dark = block(css, ':root[data-theme="dark"]');
const themable = Object.keys(root).filter((name) => THEMABLE.test(name));
const themed = themable.filter((name) => root[name].includes('light-dark('));

describe('dark theme tokens', () => {
  it('reads the stylesheet', () => {
    // A selector that stopped matching, or a branch parse that found nothing,
    // would leave every check below looking at nothing, and passing.
    expect(Object.keys(root).length).toBeGreaterThan(50);
    expect(themable.length).toBeGreaterThan(30);
    expect(themed.length).toBeGreaterThan(20);
    const balanced = (text) => text.split('(').length === text.split(')').length;
    const misread = themed.filter((name) => !branches(root[name]).every(([light, darkValue]) => (
      light && darkValue && balanced(light) && balanced(darkValue)
    )));
    expect(misread).toEqual([]);
    expect(dark).not.toBeNull();
  });

  it('gives every themable token of :root a dark value in light-dark()', () => {
    const missing = themable.filter((name) => !NEUTRAL.includes(name) && !themed.includes(name));
    expect(missing).toEqual([]);
  });

  it('writes two different values in every light-dark()', () => {
    const flat = themed.filter((name) => branches(root[name]).some(([light, darkValue]) => light === darkValue));
    expect(flat).toEqual([]);
  });

  it('switches only color-scheme in the dark block', () => {
    const code = dark.replace(/\/\*[\s\S]*?\*\//g, '');
    const restated = [...code.matchAll(/([\w-]+)\s*:/g)].map((m) => m[1]).filter((name) => name !== 'color-scheme');
    expect(restated).toEqual([]);
  });

  it('keeps every neutral exception a live, unthemed token', () => {
    const stale = NEUTRAL.filter((name) => !(name in root) || themed.includes(name));
    expect(stale).toEqual([]);
  });

  it('leaves light-dark() to design-system.css', () => {
    // A style string built in a script tests the theme as surely as a scoped
    // rule. The gallery's foundations.js is the one exception: it reads
    // light-dark() out of the stylesheet, it does not write one.
    const READER = join(SRC, 'components/gallery/foundations.js');
    const files = sourceFiles(SRC).filter((file) => file !== DESIGN_SYSTEM && file !== READER);
    expect(files.length).toBeGreaterThan(150);
    const testing = files
      .filter((file) => /light-dark\(/.test(readFileSync(file, 'utf8')))
      .map((file) => relative(SRC, file));
    expect(testing).toEqual([]);
  });
});
