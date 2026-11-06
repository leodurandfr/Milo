// frontend/tests/architecture/railHeight.test.js
/**
 * A slider's rail and everything drawn in its place are one box: the
 * multiroom row's skeleton while it loads, its offline label, its
 * external-volume label ("not managed by Milō") all stand where the rail
 * stands, so a row keeps one shape whatever it shows. The thickness is one
 * token, `--rail-height`, declared in design-system.css with its phone value
 * beside it, and every one of those boxes reads it.
 *
 * What breaks, silently: a box restating the thickness in pixels. The
 * external-volume label did — 36px against the rail's 32 — and the skeleton
 * filled its 40px wrapper, so a row grew and shrank as it loaded or as a
 * speaker switched to an external amplifier, seen only by an eye comparing two
 * rows. A phone override restating 28px drifts the same way when the token
 * moves.
 *
 * RAIL_BOXES names the boxes. A new stand-in for a rail belongs in it.
 * Mounts nothing: it reads the sources the browser reads.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = resolve(HERE, '../../src');
const DESIGN_SYSTEM = join(SRC, 'assets/styles/design-system.css');

const TOKEN = 'var(--rail-height)';

/**
 * [file, last compound of the selector, property]. A rule matches when the
 * last compound of one of its selectors carries exactly these classes, under
 * any ancestor and in any @media: `.volume-wrapper .client-offline` is the
 * offline label, `.slider-container.horizontal.labeled` is not the bare
 * horizontal slider.
 */
const RAIL_BOXES = [
  ['components/ui/RangeSlider.vue', '.slider-container', '--track-thickness'],
  ['components/ui/RangeSlider.vue', '.slider-container.horizontal', 'height'],
  ['components/ui/RangeSlider.vue', '.slider-container.vertical', 'width'],
  ['components/ui/RangeSlider.vue', '.slider-rail', 'height'],
  ['components/ui/DoubleRangeSlider.vue', '.double-range-slider', '--track-thickness'],
  ['components/ui/DoubleRangeSlider.vue', '.slider-rail', 'height'],
  ['components/ui/DoubleRangeSlider.vue', '.slider-value', 'height'],
  ['components/multiroom/MultiroomItem.vue', '.volume-skeleton', 'height'],
  ['components/multiroom/MultiroomItem.vue', '.client-offline', 'height'],
  ['components/multiroom/MultiroomItem.vue', '.client-external-volume', 'height'],
];

function style(file) {
  const source = readFileSync(join(SRC, file), 'utf8');
  return [...source.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)]
    .map((m) => m[1]).join('\n').replace(/\/\*[\s\S]*?\*\//g, '');
}

/** [{ selectors, body }] of the innermost rules, a rule inside @media included. */
function rules(css) {
  return [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)]
    .map((m) => ({ selectors: m[1].trim().split(',').map((s) => s.trim()), body: m[2] }))
    .filter((rule) => !rule.selectors[0].startsWith('@'));
}

function classesOf(compound) {
  return [...compound.matchAll(/\.([\w-]+)/g)].map((m) => m[1]).sort().join('.');
}

function lastCompound(selector) {
  return selector.split(/[\s>+~]+/).filter(Boolean).at(-1);
}

/**
 * Every value `property` takes in a rule whose selector ends on `compound`;
 * `keep` narrows which of those selectors count.
 */
function valuesOf(css, compound, property, keep = () => true) {
  const wanted = classesOf(compound);
  const declaration = new RegExp(`(?:^|;|\\s)${property}\\s*:\\s*([^;]+)`, 'g');
  return rules(css)
    .filter((rule) => rule.selectors.some((s) => keep(s) && classesOf(lastCompound(s)) === wanted))
    .flatMap((rule) => [...rule.body.matchAll(declaration)].map((m) => m[1].trim()));
}

/**
 * The other properties that size the same axis. A `min-height: 36px` beside
 * `height: var(--rail-height)` wins and draws a 36px box, so each of them is
 * either absent or the token too. A vertical slider's rail runs along its
 * height, so its length (`.vertical .slider-rail { min-height }`) is not a
 * thickness and its selectors are left out of the height axis.
 */
const SAME_AXIS = {
  height: ['min-height', 'max-height', 'block-size', 'min-block-size', 'max-block-size'],
  width: ['min-width', 'max-width', 'inline-size', 'min-inline-size', 'max-inline-size'],
  '--track-thickness': [],
};
const HORIZONTAL = (selector) => !selector.includes('.vertical');

describe('rail height', () => {
  it('is declared once per viewport in design-system.css', () => {
    const css = readFileSync(DESIGN_SYSTEM, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
    const declared = [...css.matchAll(/--rail-height\s*:\s*([^;]+);/g)].map((m) => m[1].trim());
    expect(declared).toHaveLength(2);
    declared.forEach((value) => expect(value).toMatch(/^\d+px$/));

    const mobile = css.indexOf('@media (max-aspect-ratio: 4/3)');
    expect(mobile).toBeGreaterThan(-1);
    expect(css.indexOf('--rail-height')).toBeLessThan(mobile);
    expect(css.indexOf('--rail-height', mobile)).toBeGreaterThan(mobile);
  });

  it.each(RAIL_BOXES)('%s %s takes its %s from the token', (file, compound, property) => {
    const values = valuesOf(style(file), compound, property);
    // An empty read is a renamed class, not a pass.
    expect(values.length).toBeGreaterThan(0);
    values.forEach((value) => expect(value, `${compound} { ${property}: ${value} }`).toBe(TOKEN));
  });

  it.each(RAIL_BOXES)('%s %s has nothing else sizing its %s', (file, compound, property) => {
    const keep = property === 'height' ? HORIZONTAL : () => true;
    SAME_AXIS[property].forEach((other) => {
      valuesOf(style(file), compound, other, keep)
        .forEach((value) => expect(value, `${compound} { ${other}: ${value} }`).toBe(TOKEN));
    });
  });
});
