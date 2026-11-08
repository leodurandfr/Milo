// frontend/tests/architecture/mobileBreakpoint.test.js
/**
 * Every rule that switches between the mobile and the desktop layout reads the
 * same condition, `constants/layout.js::MOBILE_QUERY`.
 *
 * CSS cannot import it, so the condition is spelled out in every `@media` rule
 * that needs it — some eighty of them — and `useIsMobile()` asks the same
 * question of `matchMedia` for the components that branch in script. What
 * breaks, silently: one rule left on another condition. When the breakpoint
 * moved from a ratio alone to a ratio *and* a width, a single rule still on
 * `(max-aspect-ratio: 4/3)` would have kept its phone spacing (or its phone
 * dock) on a large square screen that every other rule draws as a desktop —
 * visible only on such a screen.
 *
 * So any `@media` whose condition reads an aspect ratio must be the canonical
 * query, its negation, or the canonical query narrowed by one more feature;
 * and script never measures the viewport for itself — it asks useIsMobile().
 * A rule that is not about layout (which device is this?) does not belong on
 * this condition at all: `utils/kiosk.js::isKiosk()` answers that.
 * Mounts nothing: it reads the sources the browser reads.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve, relative } from 'node:path';
import { stripComments } from '../helpers/stripComments.js';
import { MOBILE_QUERY } from '../../src/constants/layout.js';

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC_DIR = resolve(HERE, '../../src');

function walk(dir, pattern) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return walk(path, pattern);
    return pattern.test(name) ? [path] : [];
  });
}

const ACCEPTED = [
  MOBILE_QUERY,
  `not (${MOBILE_QUERY})`,
];

function accepted(condition) {
  return ACCEPTED.includes(condition) || condition.startsWith(`${MOBILE_QUERY} and (`);
}

/** Every `@media` condition in the stylesheets that reads an aspect ratio, with where it is. */
function layoutConditions() {
  return walk(SRC_DIR, /\.(vue|css)$/).flatMap((file) => {
    const source = stripComments(readFileSync(file, 'utf8'));
    return [...source.matchAll(/@media\s+([^{]+)\{/g)]
      .map((match) => match[1].trim().replace(/\s+/g, ' '))
      .filter((condition) => condition.includes('aspect-ratio'))
      .map((condition) => ({ file: relative(SRC_DIR, file), condition }));
  });
}

describe('mobile breakpoint', () => {
  const conditions = layoutConditions();

  it('finds the layout rules it guards', () => {
    // An empty read is a broken scan, not a pass: the mobile layout spans dozens of files.
    expect(conditions.length).toBeGreaterThan(50);
  });

  it('accepts the canonical forms and refuses a drifted one', () => {
    expect(accepted(MOBILE_QUERY)).toBe(true);
    expect(accepted(`not (${MOBILE_QUERY})`)).toBe(true);
    expect(accepted(`${MOBILE_QUERY} and (display-mode: standalone)`)).toBe(true);
    expect(accepted('(max-aspect-ratio: 4/3)')).toBe(false);
    expect(accepted('not (max-aspect-ratio: 4/3)')).toBe(false);
    expect(accepted('(min-aspect-ratio: 4/3)')).toBe(false);
  });

  it('is the one condition every layout rule reads', () => {
    const drifted = conditions.filter(({ condition }) => !accepted(condition));
    expect(drifted.map(({ file, condition }) => `${file}: @media ${condition}`)).toEqual([]);
  });

  it('is the condition useIsMobile() asks matchMedia', () => {
    const source = stripComments(readFileSync(join(SRC_DIR, 'composables/useIsMobile.js'), 'utf8'));
    expect(source).toMatch(/import \{ MOBILE_QUERY \} from '@\/constants\/layout'/);
    expect(source).toMatch(/matchMedia\(MOBILE_QUERY\)/);
  });

  it('is never worked out again in script', () => {
    // A component that measured the viewport itself kept the ratio-only
    // breakpoint when the CSS moved on, and drew its EQ bands in the phone
    // shape inside a desktop modal. Script asks useIsMobile(); matchMedia is
    // left to it and to `prefers-*` queries, which are not layout.
    const found = walk(SRC_DIR, /\.(vue|js)$/).flatMap((file) => {
      const source = stripComments(readFileSync(file, 'utf8')).replace(/<style[\s\S]*?<\/style>/g, '');
      const where = relative(SRC_DIR, file);
      const handRatio = /innerWidth\s*\/\s*(?:window\.)?innerHeight|innerHeight\s*\/\s*(?:window\.)?innerWidth/.test(source)
        ? [`${where}: viewport ratio computed by hand`] : [];
      const queries = [...source.matchAll(/matchMedia\??\.?\(\s*([^)]*?)\s*\)/g)]
        .map((match) => match[1])
        .filter((arg) => !/^['"`]\(prefers-/.test(arg))
        .filter((arg) => !(arg === 'MOBILE_QUERY' && where === join('composables', 'useIsMobile.js')))
        .map((arg) => `${where}: matchMedia(${arg})`);
      return [...handRatio, ...queries];
    });
    expect(found).toEqual([]);
  });
});
