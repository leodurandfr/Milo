// frontend/tests/architecture/colorTokens.test.js
/**
 * The color system's two tiers, held in place (design-system.css explains
 * them): a private gray palette, and role tokens picked from it, each with
 * its light and dark value.
 *
 * Five drifts break it, all silently. A component reading a token nobody
 * declares — a role renamed on one side — paints nothing at all, and the
 * browser says so nowhere. A component declaring its own `--color-*` is a
 * second palette the roles cannot see. A component reading a palette step
 * directly bypasses the role, so it stops following the theme. A neutral
 * written as a hex in a role is a gray from outside the palette, which is how
 * the greens crept into the dark theme. And a palette entry no role reads is
 * dead weight that hides the ones that matter.
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
const SECTION = sectionsOf(LIGHT_BODY);
/** Every custom property design-system.css declares anywhere. */
const DECLARED = new Set([...CSS.matchAll(/(--[\w-]+)\s*:/g)].map((m) => m[1]));

describe('color tokens', () => {
  it('reads the stylesheet and the sources', () => {
    // A selector or a walk that stopped matching would leave every check
    // below looking at nothing, and passing.
    expect(Object.keys(LIGHT).filter((name) => name.startsWith('--gray-')).length).toBeGreaterThan(10);
    expect(Object.keys(LIGHT).filter((name) => name.startsWith('--color-')).length).toBeGreaterThan(30);
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
        stray.push(`${where}: ${name}`);
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

  it('picks every neutral role from the palette', () => {
    // Both themes at once: a role's dark value sits beside its light one in
    // light-dark().
    const written = Object.entries(LIGHT)
      .filter(([name]) => name.startsWith('--color-') && !NOT_NEUTRAL.includes(SECTION[name]))
      .filter(([, value]) => /#[0-9a-f]{3,8}\b|rgba?\(|hsla?\(/i.test(value))
      .map(([name, value]) => `${name}: ${value}`);

    expect(written).toEqual([]);
  });

  it('reads every palette step and tint', () => {
    // An entry nothing reads is how the palette grew to twice what the roles
    // needed: retuned or merged roles leave their old steps behind, unseen. A
    // tint counts if a role or a rule reads it, a step if one does or a tint
    // that counts is mixed from it — never a comment, never its own tint alone.
    const code = CSS.replace(/\/\*[\s\S]*?\*\//g, '');
    const palette = Object.keys(LIGHT).filter((name) => name.startsWith('--gray-'));
    const readers = code.replace(/^\s*--gray-[\w-]+\s*:[^;]*;/gm, '');
    const read = (name) => new RegExp(`var\\(\\s*${name}\\s*\\)`).test(readers);
    const tints = palette.filter((name) => /-a\d+$/.test(name));
    const liveTints = tints.filter(read);
    const steps = palette.filter((name) => !tints.includes(name));
    expect(tints.length).toBeGreaterThan(10);
    expect(steps.length).toBeGreaterThan(10);

    const unread = [
      ...tints.filter((name) => !liveTints.includes(name)),
      ...steps.filter((name) => !read(name) && !liveTints.some((tint) => tint.startsWith(`${name}-a`))),
    ];
    expect(unread).toEqual([]);
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

    const inline = Object.entries(LIGHT)
      .filter(([name]) => name.startsWith('--color-') && !NOT_NEUTRAL.includes(SECTION[name]))
      .filter(([, value]) => /color-mix\(/.test(value))
      .map(([name, value]) => `${name}: ${value}`);
    expect(inline).toEqual([]);
  });
});
