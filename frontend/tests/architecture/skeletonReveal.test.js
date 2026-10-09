// frontend/tests/architecture/skeletonReveal.test.js
/**
 * Every wait drawn as a skeleton ends with one of two motions: `fade-slide`
 * when a block of skeletons hands over to a block of content, and the reveal
 * (`--transition-reveal`, the `reveal` transition, LazyImage's `skeleton`) when
 * a skeleton hands over to its content in place. Before the reveal there were
 * five, each timed in its own file: 200 ms on the images, 300 ms on the radio
 * tiles, 450 ms on the multiroom rows, 400/300 ms on the update lists, none on
 * the system info.
 *
 * What breaks, silently: a component timing its skeleton again — a literal
 * duration, its own Vue transition — which only an eye comparing two screens
 * catches. Mounts nothing: it reads the sources the browser reads.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, relative, resolve } from 'node:path';
import { stripComments } from '../helpers/stripComments';

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = resolve(HERE, '../../src');
const LAZY_IMAGE = join(SRC, 'components/ui/LazyImage.vue');

const REVEAL = 'var(--transition-reveal)';
/** The two motions a skeleton may leave with, and `none` (no motion at all). */
const SKELETON_TRANSITIONS = new Set(['reveal', 'fade-slide', 'none']);

function vueFiles(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return vueFiles(path);
    return entry.name.endsWith('.vue') ? [path] : [];
  });
}

function parts(file) {
  const source = readFileSync(file, 'utf8');
  const template = /<template>([\s\S]*)<\/template>/.exec(source)?.[1] ?? '';
  const style = [...source.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)].map((m) => m[1]).join('\n');
  return { template: stripComments(template), style: style.replace(/\/\*[\s\S]*?\*\//g, '') };
}

/** [{ selector, body }] of the innermost rules (a rule inside @media included). */
function rules(style) {
  return [...style.matchAll(/([^{}]+)\{([^{}]*)\}/g)]
    .map((m) => ({ selector: m[1].trim(), body: m[2] }))
    .filter((rule) => !rule.selector.startsWith('@'));
}

/** The values of the rule's `transition` declarations that move opacity. */
function opacityTransitions(body) {
  return [...body.matchAll(/(?:^|;|\s)transition\s*:\s*([^;]+)/g)]
    .map((m) => m[1].trim())
    .filter((value) => /\b(opacity|all)\b/.test(value));
}

/** The names a `<Transition>` wrapping a shimmer can take, per file. */
function transitionsAroundShimmers(template) {
  const found = [];
  for (const m of template.matchAll(/<[Tt]ransition\b([^>]*)>([\s\S]*?)<\/[Tt]ransition>/g)) {
    if (!/\bshimmer\b/.test(m[2]) && !/<Skeleton\w+/.test(m[2])) continue;
    const literal = /\sname="([^"]+)"/.exec(m[1]);
    const bound = /\s:name="([^"]+)"/.exec(m[1]);
    const names = literal ? [literal[1]] : [...(bound?.[1] ?? '').matchAll(/'([^']*)'/g)].map((n) => n[1]);
    found.push(names.length ? names : ['(unnamed)']);
  }
  return found;
}

const FILES = vueFiles(SRC).filter((file) => !file.includes('/gallery/'));
const DRAWING = FILES.map((file) => ({ file, ...parts(file) })).filter(({ template }) => /\bshimmer\b/.test(template));

describe('the end of a skeleton', () => {
  it('reads the components that draw one', () => {
    // A template or style pattern that stopped matching would leave every
    // check below looking at nothing, and passing.
    expect(DRAWING.length).toBeGreaterThan(15);
    expect(DRAWING.flatMap(({ style }) => rules(style)).length).toBeGreaterThan(250);
    expect(DRAWING.flatMap(({ template }) => transitionsAroundShimmers(template)).length).toBeGreaterThan(8);
  });

  it('times a skeleton swapped by a class with the reveal, and the content swapped with it', () => {
    // MultiroomItem's shape: the skeleton and its content both stay mounted,
    // and one class (`visible`) moves each in or out. A rule naming either
    // carries the reveal.
    const timed = [];
    const offTime = [];
    for (const { file, style } of DRAWING) {
      const all = rules(style);
      const skeletonRules = all.filter((rule) => /skeleton/.test(rule.selector));
      const toggles = new Set(skeletonRules.flatMap((rule) =>
        [...rule.selector.matchAll(/\.([\w-]+)/g)].map((m) => m[1]).filter((c) => !/skeleton|shimmer/.test(c))));
      const swapped = all.filter((rule) => /skeleton/.test(rule.selector)
        || [...toggles].some((toggle) => new RegExp(`\\.${toggle}\\b`).test(rule.selector)));
      for (const rule of swapped) {
        for (const value of opacityTransitions(rule.body)) {
          timed.push(value);
          if (!value.includes(REVEAL) && value !== 'none') offTime.push(`${relative(SRC, file)}: ${rule.selector} → ${value}`);
        }
      }
    }
    expect(timed.length).toBeGreaterThan(10);
    expect(offTime).toEqual([]);
  });

  it('hands a skeleton over through the reveal or a fade-slide, never a transition of its own', () => {
    const own = [];
    for (const { file, template } of DRAWING) {
      for (const names of transitionsAroundShimmers(template)) {
        if (names.some((name) => !SKELETON_TRANSITIONS.has(name))) own.push(`${relative(SRC, file)}: ${names.join(' | ')}`);
      }
    }
    expect(own).toEqual([]);
  });

  it('reveals every image with the reveal', () => {
    // LazyImage draws every cover, logo and avatar, over its skeleton or its
    // placeholder: its two fades are the reveal or every image drifts at once.
    const { style } = parts(LAZY_IMAGE);
    const layers = rules(style).filter((rule) => /^\.lazy-image-(main|placeholder)$/.test(rule.selector));
    expect(layers.map((rule) => rule.selector).sort()).toEqual(['.lazy-image-main', '.lazy-image-placeholder']);
    expect(layers.flatMap((rule) => opacityTransitions(rule.body))).toEqual([`opacity ${REVEAL}`, `opacity ${REVEAL}`]);
  });

  it('plays the reveal on LazyImage\'s skeleton when its hooks animate it', () => {
    // The skeleton's transition is named `reveal` but played by JS hooks
    // (`:css="false"`, no forced layout per cover): the name alone proves
    // nothing, so both hooks must be wired and the code they run must time it
    // from the token itself — read without comments, which can name a token
    // the code no longer reads.
    const source = readFileSync(LAZY_IMAGE, 'utf8');
    const { template } = parts(LAZY_IMAGE);
    const script = stripComments(/<script setup>([\s\S]*?)<\/script>/.exec(source)?.[1] ?? '');
    const skeletonTransition = /<Transition\b([^>]*)>\s*<div[^>]*lazy-image-skeleton/.exec(template);
    expect(skeletonTransition).not.toBeNull();
    const attrs = skeletonTransition[1];
    if (!/:css="false"/.test(attrs)) return;
    const hooks = ['enter', 'leave'].map((hook) => new RegExp(`@${hook}="(\\w+)"`).exec(attrs)?.[1]);
    expect(hooks.every(Boolean)).toBe(true);
    for (const hook of hooks) expect(script).toMatch(new RegExp(`\\b${hook}\\s*=\\s*\\(el, done\\) => revealFade\\(`));
    expect(script).toMatch(/function revealFade[\s\S]*transitionTiming\('--transition-reveal'\)/);
  });
});
