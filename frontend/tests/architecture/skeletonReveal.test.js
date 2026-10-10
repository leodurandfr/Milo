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
    .flatMap((m) => m[1].split(/,(?![^(]*\))/))
    .map((value) => value.trim())
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

/**
 * The states of every fade-slide swap in a .swap-stack: [{ head, body }], `head`
 * the state's own opening tag (a self-closing state is all head), `body` all of
 * it. A state is a child of the transition at its first indentation; a swap
 * nested in a state is read as a swap of its own. The stack is any element
 * whose class or :class names swap-stack, its attributes read quote-aware (an
 * arrow function's `>` inside one does not end the tag).
 */
function swapStates(template) {
  const states = [];
  const attrs = '(?:[^>"]|"[^"]*")*';
  const open = new RegExp(
    `<[\\w-]+\\b${attrs}\\s:?class="[^"]*\\bswap-stack\\b[^"]*"${attrs}>\\s*`
      + `<Transition\\b${attrs}\\sname="fade-slide"${attrs}>`,
    'g',
  );
  for (const m of template.matchAll(open)) {
    // The body up to this transition's own close, across nested ones.
    let depth = 1;
    let at = m.index + m[0].length;
    const tags = /<(\/?)Transition\b[^>]*>/g;
    tags.lastIndex = at;
    let end = template.length;
    for (let t = tags.exec(template); t; t = tags.exec(template)) {
      depth += t[1] ? -1 : 1;
      if (depth === 0) { end = t.index; break; }
    }
    const body = template.slice(at, end);
    const indent = /\n([ \t]*)<[\w-]/.exec(body)?.[1];
    if (indent === undefined) continue;
    const lines = body.split('\n');
    let current = null;
    for (const line of lines) {
      if (line.startsWith(`${indent}<`) && !line.startsWith(`${indent}</`)) {
        current = { head: [], body: [], open: true };
        states.push(current);
      }
      if (!current) continue;
      current.body.push(line);
      // The head runs until the state's first child, one level deeper.
      if (current.open && new RegExp(`^${indent}[ \\t]+<[\\w-]`).test(line)) current.open = false;
      if (current.open) current.head.push(line);
    }
  }
  return states.map(({ head, body }) => ({ head: head.join('\n'), body: body.join('\n') }));
}

/** The Skeleton* components whose own root already carries .swap-skeleton. */
const SELF_SWAPPING = new Set(vueFiles(join(SRC, 'components'))
  .filter((file) => /\/Skeleton\w+\.vue$/.test(file))
  .filter((file) => /^\s*<\w+[^>]*\bswap-skeleton\b/.test(parts(file).template))
  .map((file) => /\/(Skeleton\w+)\.vue$/.exec(file)[1]));

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

  it('hands a page-sized skeleton over in place, marked .swap-skeleton', () => {
    // A state of a fade-slide swap that is a skeleton is drawn in the place
    // its content takes, so the two crossfade there (design-system.css:
    // .swap-skeleton) rather than the skeleton rising away under content
    // coming up from below. An unmarked one still swaps — with a jump only an
    // eye catches.
    const states = FILES.flatMap((file) => swapStates(parts(file).template).map((state) => ({ file, ...state })));
    // A skeleton state: a Skeleton* component as the state itself, or a state
    // keyed as the wait (`loading…`, `skeleton`) drawing skeletons. Content
    // that ends on a load-more skeleton is not one, and a state holding a
    // swap of its own is read through that swap.
    const skeletons = states.filter(({ head, body }) => {
      if (/\bswap-stack\b/.test(body)) return false;
      if (/^\s*<Skeleton\w+/.test(head)) return true;
      const key = /\skey="([^"]*)"/.exec(head)?.[1] ?? '';
      return /loading|skeleton/i.test(key) && (/\bshimmer\b/.test(body) || /<Skeleton\w+/.test(body));
    });
    expect(new Set(states.map(({ file }) => file)).size).toBeGreaterThan(20);
    expect(new Set(skeletons.map(({ file }) => file)).size).toBeGreaterThan(10);
    const unmarked = skeletons
      .filter(({ head }) => !/\bswap-skeleton\b/.test(head))
      .filter(({ head }) => !SELF_SWAPPING.has(/^\s*<(\w+)/.exec(head)?.[1]))
      .map(({ file, head }) => `${relative(SRC, file)}: ${head.trim().split('\n')[0]}`);
    expect(unmarked).toEqual([]);
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
