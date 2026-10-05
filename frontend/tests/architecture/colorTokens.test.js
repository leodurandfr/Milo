// frontend/tests/architecture/colorTokens.test.js
/**
 * The color system's two tiers, held in place (design-system.css explains
 * them): a private gray palette, and role tokens picked from it in both theme
 * blocks.
 *
 * Four drifts break it, all silently. A component reading a token nobody
 * declares — a role renamed on one side — paints nothing at all, and the
 * browser says so nowhere. A component declaring its own `--color-*` is a
 * second palette the theme blocks cannot see. A component reading a palette
 * step directly bypasses the role, so it stops following the theme. And a
 * neutral written as a hex in a theme block is a gray from outside the
 * palette, which is how the greens crept into the dark theme.
 *
 * Mounts nothing: it reads the source the browser reads.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, relative, resolve } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = resolve(HERE, '../../src');
const DESIGN_SYSTEM = join(SRC, 'assets/styles/design-system.css');
const CSS = readFileSync(DESIGN_SYSTEM, 'utf8');

const ROLE = /--(?:color|stroke|gradient)-[\w-]+/;

/** The contextual roles, and the one file allowed to redeclare each (see design-system.css). */
const CONTEXTUAL = {
  '--color-panel': ['components/ui/Modal.vue'],
  '--color-inset': ['components/ui/Modal.vue'],
  '--color-tile': ['components/ui/Modal.vue'],
  '--color-header': ['components/ui/Modal.vue'],
  '--color-header-control': ['components/ui/Modal.vue'],
  '--color-header-text': ['components/ui/Modal.vue'],
  '--color-header-text-secondary': ['components/ui/Modal.vue'],
  '--color-control': ['components/ui/NavigationHeader.vue'],
  '--color-text': ['components/ui/NavigationHeader.vue'],
  '--color-text-secondary': ['components/ui/NavigationHeader.vue']
};

/**
 * Sections whose values are not neutrals and so are written as values: the
 * brand, the status hues, and the gradients and strokes, which are one-off
 * compositions belonging to no ramp.
 */
const NOT_NEUTRAL = ['PALETTE', 'BRAND', 'STATUS', 'STROKES', 'SOURCE GRADIENTS'];

function sourceFiles(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return sourceFiles(path);
    return /\.(vue|js|css)$/.test(entry.name) ? [path] : [];
  });
}

/** The body of the first rule whose selector is exactly `selector`. */
function block(selector) {
  const opener = new RegExp(`^${selector.replace(/[[\]"=]/g, '\\$&')}\\s*\\{`, 'm');
  const match = opener.exec(CSS);
  if (!match) return '';
  return CSS.slice(match.index + match[0].length, CSS.indexOf('}', match.index));
}

/** name -> value of a block, comments dropped. */
function declarations(body) {
  const code = body.replace(/\/\*[\s\S]*?\*\//g, '');
  return Object.fromEntries([...code.matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)].map((m) => [m[1], m[2].trim()]));
}

/** name -> section title, for the tokens of the light block. */
function sectionsOf(body) {
  const owner = {};
  let current = null;
  for (const match of body.matchAll(/\/\*\s*===\s*([^=]+?)\s*===\s*\*\/|\/\*[\s\S]*?\*\/|(--[\w-]+)\s*:/g)) {
    if (match[1]) current = match[1];
    else if (match[2]) owner[match[2]] = current;
  }
  return owner;
}

const FILES = sourceFiles(SRC);
const LIGHT_BODY = block(':root');
const LIGHT = declarations(LIGHT_BODY);
const DARK = declarations(block(':root[data-theme="dark"]'));
const SECTION = sectionsOf(LIGHT_BODY);
/** Every custom property design-system.css declares anywhere. */
const DECLARED = new Set([...CSS.matchAll(/(--[\w-]+)\s*:/g)].map((m) => m[1]));

describe('color tokens', () => {
  it('reads the stylesheet and the sources', () => {
    // A selector or a walk that stopped matching would leave every check
    // below looking at nothing, and passing.
    expect(Object.keys(LIGHT).filter((name) => name.startsWith('--gray-')).length).toBeGreaterThan(10);
    expect(Object.keys(DARK).length).toBeGreaterThan(20);
    expect(FILES.length).toBeGreaterThan(150);
    expect(new Set(Object.values(SECTION)).size).toBeGreaterThan(10);
  });

  it('references no color, stroke or gradient token that is not declared', () => {
    const referenced = [];
    const missing = [];
    for (const file of FILES) {
      if (file === DESIGN_SYSTEM) continue;
      for (const [, name] of readFileSync(file, 'utf8').matchAll(/var\(\s*(--[\w-]+)/g)) {
        if (!ROLE.test(name)) continue;
        referenced.push(name);
        if (!DECLARED.has(name)) missing.push(`${relative(SRC, file)}: ${name}`);
      }
    }

    expect(referenced.length).toBeGreaterThan(500);
    expect([...new Set(missing)]).toEqual([]);
  });

  it('declares color tokens only in design-system.css', () => {
    const stray = [];
    for (const file of FILES) {
      if (file === DESIGN_SYSTEM) continue;
      const where = relative(SRC, file);
      for (const [, name] of readFileSync(file, 'utf8').matchAll(/(?:^|[\s;{])(--color-[\w-]+)\s*:/g)) {
        if (!CONTEXTUAL[name]?.includes(where)) stray.push(`${where}: ${name}`);
      }
    }

    expect(stray).toEqual([]);
  });

  it('keeps the gray palette private to design-system.css', () => {
    const readers = FILES
      .filter((file) => file !== DESIGN_SYSTEM && /var\(\s*--(?:gray-|black\b)/.test(readFileSync(file, 'utf8')))
      .map((file) => relative(SRC, file));

    expect(readers).toEqual([]);
  });

  it('picks every neutral of both theme blocks from the palette', () => {
    const written = [];
    for (const [theme, values] of [['light', LIGHT], ['dark', DARK]]) {
      for (const [name, value] of Object.entries(values)) {
        if (!name.startsWith('--color-') || NOT_NEUTRAL.includes(SECTION[name])) continue;
        if (/#[0-9a-f]{3,8}\b|rgba?\(|hsla?\(/i.test(value)) written.push(`${theme} ${name}: ${value}`);
      }
    }

    expect(written).toEqual([]);
  });

  it('writes every translucent neutral as a palette tint, named for its step and alpha', () => {
    // A role mixing its own alpha is a second spelling of a tint: two roles
    // land on one value written twice, and retuning one leaves the other. A
    // tint whose name disagrees with its value misleads every role reading it.
    const TINT = /^--gray-(\d+)-a(\d+)$/;
    const tints = Object.entries(LIGHT).filter(([name]) => TINT.test(name));
    expect(tints.length).toBeGreaterThan(10);

    const misnamed = tints
      .filter(([name, value]) => {
        const [, step, alpha] = TINT.exec(name);
        return value !== `color-mix(in srgb, var(--gray-${step}) ${alpha}%, transparent)`;
      })
      .map(([name, value]) => `${name}: ${value}`);
    expect(misnamed).toEqual([]);

    const inline = [];
    for (const [theme, values] of [['light', LIGHT], ['dark', DARK]]) {
      for (const [name, value] of Object.entries(values)) {
        if (name.startsWith('--color-') && /color-mix\(/.test(value)) inline.push(`${theme} ${name}: ${value}`);
      }
    }
    expect(inline).toEqual([]);
  });
});
