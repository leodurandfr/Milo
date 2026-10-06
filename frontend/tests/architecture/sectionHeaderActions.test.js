// frontend/tests/architecture/sectionHeaderActions.test.js
/**
 * Structural guardrail over the controls beside a settings section's title.
 *
 * Those controls were drawn five different ways — Add a server in `outline`,
 * Create a zone in `brand`, Reorder in `background-strong`, Qobuz's Disconnect
 * at `medium` — because half the sections hand-rolled their header and the
 * other half passed whatever Button they liked into `SectionHeader`'s slot. A
 * slot accepts anything, so the component alone could only align them, never
 * make them look alike. This holds the two halves:
 *
 *   1. a control in a section `#header` sits in `SectionHeader`'s `#actions`
 *      (layout lives there once), never beside it;
 *   2. an action is a Button or a Dropdown — IconButton has no label —, a
 *      Button is `small` and `tinted` — `brand` only
 *      for a state asking for the user's attention now (a preset edited and
 *      unsaved, Done while reordering, a remote to pair) — and a Dropdown is
 *      `small` in its default `filled`.
 *
 * `brand` cannot be told apart from a misuse by reading a template; that half
 * stays a review call. A header whose only control is a Toggle is
 * `ToggleSection`'s shape and is not concerned.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve, relative } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC_DIR = resolve(HERE, '../../src');

const BUTTON_VARIANTS = new Set(['tinted', 'brand']);
const DROPDOWN_VARIANTS = new Set(['filled']);
const CONTROLS = ['Button', 'IconButton', 'Dropdown'];

function vueFiles(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return vueFiles(path);
    return name.endsWith('.vue') ? [path] : [];
  });
}

function templateOf(source) {
  const start = source.indexOf('<template');
  const end = source.lastIndexOf('</template>');
  return start === -1 ? '' : source.slice(start, end);
}

/** The opening tag starting at `from`, read up to its `>` outside quotes. */
function openingTag(text, from) {
  let quote = null;
  for (let i = from; i < text.length; i++) {
    const c = text[i];
    if (quote) {
      if (c === quote) quote = null;
    } else if (c === '"' || c === "'") {
      quote = c;
    } else if (c === '>') {
      return text.slice(from, i + 1);
    }
  }
  throw new Error(`unterminated tag at ${from}`);
}

/** Inner text of the element whose opening tag starts at `from`, nesting-aware. */
function innerOf(text, from, tag) {
  const open = openingTag(text, from);
  if (open.endsWith('/>')) return '';
  const opener = new RegExp(`<${tag}(?=[\\s>/])`, 'g');
  const closer = `</${tag}>`;
  let depth = 1;
  let i = from + open.length;
  while (depth > 0) {
    opener.lastIndex = i;
    const nextOpen = opener.exec(text);
    const nextClose = text.indexOf(closer, i);
    if (nextClose === -1) throw new Error(`unclosed <${tag}> at ${from}`);
    if (nextOpen && nextOpen.index < nextClose) {
      depth += 1;
      i = nextOpen.index + openingTag(text, nextOpen.index).length;
    } else {
      depth -= 1;
      if (depth === 0) return text.slice(from + open.length, nextClose);
      i = nextClose + closer.length;
    }
  }
  return '';
}

/** Every `<template #slot>` inner text for one slot name. */
function slotBodies(text, slot) {
  const named = new RegExp(`\\s#${slot}(?=[\\s>=/])`);
  return [...text.matchAll(/<template(?=[\s>])/g)]
    .filter((m) => named.test(openingTag(text, m.index)))
    .map((m) => innerOf(text, m.index, 'template'));
}

/** The text with every element of one component cut out. */
function without(text, name) {
  const re = new RegExp(`<${name}(?=[\\s>/])`);
  let rest = text;
  for (let m = re.exec(rest); m; m = re.exec(rest)) {
    const open = openingTag(rest, m.index);
    const end = open.endsWith('/>')
      ? m.index + open.length
      : m.index + open.length + innerOf(rest, m.index, name).length + `</${name}>`.length;
    rest = rest.slice(0, m.index) + rest.slice(end);
  }
  return rest;
}

/** Opening tags of one component inside a text. */
function tags(text, name) {
  const re = new RegExp(`<${name}(?=[\\s>/])`, 'g');
  return [...text.matchAll(re)].map((m) => openingTag(text, m.index));
}

/**
 * Values an attribute can take: the literal, or the literals of a bound
 * `'a'` / `cond ? 'a' : 'b'`. Anything else — a variable in any branch — is
 * `null`, unreadable, and fails: a guardrail that skipped it would pass on
 * whatever the variable holds.
 */
function attrValues(tag, attr) {
  const bound = tag.match(new RegExp(`(?:v-bind)?:${attr}="([^"]*)"`));
  if (bound) {
    const expr = bound[1];
    const branches = expr.includes('?') ? expr.slice(expr.indexOf('?') + 1) : expr;
    if (branches.replace(/'[^']*'/g, '').replace(/[\s:]/g, '') !== '') return null;
    return [...branches.matchAll(/'([^']*)'/g)].map((m) => m[1]);
  }
  const plain = tag.match(new RegExp(`(?<![:\\w-])${attr}="([^"]*)"`));
  return plain ? [plain[1]] : [];
}

const FILES = vueFiles(join(SRC_DIR, 'components')).map((path) => ({
  file: relative(SRC_DIR, path),
  template: templateOf(readFileSync(path, 'utf-8'))
}));

const HEADERS = FILES.flatMap(({ file, template }) =>
  slotBodies(template, 'header').map((body) => ({ file, body })));

const ACTIONS = FILES.flatMap(({ file, template }) =>
  [...template.matchAll(/<SectionHeader(?=[\s>/])/g)]
    .flatMap((m) => slotBodies(innerOf(template, m.index, 'SectionHeader'), 'actions'))
    .map((body) => ({ file, body })));

describe('section header actions', () => {
  it('reads a non-trivial surface', () => {
    expect(HEADERS.length).toBeGreaterThanOrEqual(10);
    expect(ACTIONS.length).toBeGreaterThanOrEqual(7);
    const controls = ACTIONS.flatMap(({ body }) => [...tags(body, 'Button'), ...tags(body, 'Dropdown')]);
    expect(controls.length).toBeGreaterThanOrEqual(9);
  });

  it('a control in a section header sits in SectionHeader\'s actions', () => {
    const outside = HEADERS
      .filter(({ body }) => CONTROLS.some((name) => tags(without(body, 'SectionHeader'), name).length))
      .map(({ file }) => file);
    expect(outside).toEqual([]);
  });

  it('a header action is a Button or a Dropdown, never an IconButton', () => {
    const iconOnly = ACTIONS.flatMap(({ file, body }) => tags(body, 'IconButton')
      .map((tag) => `${file}: ${tag.replace(/\s+/g, ' ')}`));
    expect(iconOnly).toEqual([]);
  });

  it('a header Button is small, and tinted or brand', () => {
    const offenders = ACTIONS.flatMap(({ file, body }) => tags(body, 'Button')
      .filter((tag) => {
        const variants = attrValues(tag, 'variant');
        const sizes = attrValues(tag, 'size');
        return !variants?.length || variants.some((v) => !BUTTON_VARIANTS.has(v))
          || sizes?.length !== 1 || sizes[0] !== 'small';
      })
      .map((tag) => `${file}: ${tag.replace(/\s+/g, ' ')}`));
    expect(offenders).toEqual([]);
  });

  it('a header Dropdown is small, in its default filled', () => {
    const offenders = ACTIONS.flatMap(({ file, body }) => tags(body, 'Dropdown')
      .filter((tag) => {
        const variants = attrValues(tag, 'variant');
        const sizes = attrValues(tag, 'size');
        return !variants || variants.some((v) => !DROPDOWN_VARIANTS.has(v))
          || sizes?.length !== 1 || sizes[0] !== 'small';
      })
      .map((tag) => `${file}: ${tag.replace(/\s+/g, ' ')}`));
    expect(offenders).toEqual([]);
  });
});
