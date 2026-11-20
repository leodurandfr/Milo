// frontend/tests/architecture/trackRowRendering.test.js
/**
 * A track list re-renders only the rows that changed.
 *
 * Two ways every mounted row used to re-render, both invisible in the markup
 * and both paid on the Pi's kiosk, where a step of the render window is sized
 * to stay under a frame (useRenderWindow.js):
 *
 * - `:playing` handed to every row. Only the current row reads it (TrackRow's
 *   `current && playing`), so a play/pause re-rendered the whole list for one
 *   indicator. The prop must be gated on the row's own `:current`.
 * - A `#menu` slot that closes over the loop variable. Vue then compiles the
 *   row with DYNAMIC_SLOTS, and `shouldUpdateComponent` answers true for such a
 *   component on every parent render, whatever its props: each scroll step of
 *   the window re-rendered every row already mounted (~n²/24 renders to scroll a
 *   list of n to its end). A row repeated by a `v-for` — its own, or its
 *   drag wrapper's — that fills `#menu` carries `v-memo` on that element, so an
 *   unchanged row is not even patched. A dep left out freezes the row, so the
 *   memo lists the loop item, the row's `:current` and its `:playing` as their
 *   own entries, and names every reactive value the repeated element and
 *   everything inside it read — a call it does not list whole is followed into
 *   the view's script, the composable that returned it, or the store.
 *
 * Read from the templates (comments stripped), outside the gallery.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { stripComments } from '../helpers/stripComments.js';

const SRC = resolve(dirname(fileURLToPath(import.meta.url)), '../../src');

// Rows v-memo cannot reach, each with why that is acceptable. Each reason is
// also checked: an entry whose reason no longer holds fails below.
const UNMEMOIZABLE = {
  // Nested in the discs' own v-for, where v-memo has no per-row cache (Vue
  // refuses it there). An album is not windowed: its rows mount once, and
  // re-render on a track change or a play/pause, not on every scroll step.
  'components/music-library/views/AlbumView.vue': 'nested v-for',
};

const VOID = new Set(['area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'source', 'track', 'wbr']);

function vueFiles(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return name === 'gallery' ? [] : vueFiles(path);
    return name.endsWith('.vue') ? [path] : [];
  });
}

function parseAttrs(tag) {
  const attrs = {};
  for (const match of tag.matchAll(/\s([:@#]?[\w.-]+)(?:="([^"]*)")?/g)) attrs[match[1]] = match[2] ?? '';
  return attrs;
}

/**
 * Each `<TrackRow>` with its attributes, its body, and the elements it sits in
 * (nearest last) — enough to find the element whose v-for renders it.
 */
function trackRows(source) {
  const rows = [];
  const stack = [];
  for (let at = source.indexOf('<'); at !== -1; at = source.indexOf('<', at + 1)) {
    if (source[at + 1] === '/') {
      const name = /^<\/([\w-]+)/.exec(source.slice(at))?.[1];
      const open = stack.map((el) => el.name).lastIndexOf(name);
      if (open !== -1) {
        const [el] = stack.splice(open);
        el.body = source.slice(el.bodyStart, at);
      }
      continue;
    }
    const name = /^<([A-Za-z][\w-]*)/.exec(source.slice(at))?.[1];
    if (!name) continue;
    let i = at + name.length + 1;
    let quote = null;
    for (; i < source.length; i++) {
      const c = source[i];
      if (quote) { if (c === quote) quote = null; } else if (c === '"' || c === "'") quote = c;
      else if (c === '>') break;
    }
    const tag = source.slice(at, i + 1);
    const el = { name, attrs: parseAttrs(tag), body: '', bodyStart: i + 1 };
    if (name === 'TrackRow') rows.push({ el, ancestors: [...stack] });
    if (!tag.endsWith('/>') && !VOID.has(name)) stack.push(el);
    at = i;
  }
  return rows.map(({ el, ancestors }) => ({ attrs: el.attrs, body: el.body, el, ancestors }));
}

const ROWS = vueFiles(SRC).flatMap((path) => {
  const [template, ...scripts] = stripComments(readFileSync(path, 'utf8')).split('<script');
  const script = scripts.join('<script');
  return trackRows(template).map((row) => {
    // The element that repeats the row: the row itself, or its drag wrapper.
    const wrapped = row.attrs['v-for'] === undefined;
    const loop = wrapped ? row.ancestors.findLast((el) => el.attrs['v-for'] !== undefined) : row;
    // Whether that element sits in a v-for of its own.
    const around = wrapped ? row.ancestors.slice(0, row.ancestors.indexOf(loop)) : row.ancestors;
    const nested = Boolean(loop) && around.some((el) => el.attrs['v-for'] !== undefined);
    return { file: relative(SRC, path), script, ...row, loop: loop?.el ?? loop, wrapped, nested };
  });
});

/** The loop's item variable: `song` in `(song, idx) in …`, `row` in `row in …`. */
const loopItem = (vFor) => /^\(?\s*([\w$]+)/.exec(vFor)[1];

/** A `v-memo="[a, b(c, d), e]"` array's entries, whitespace collapsed. */
function memoEntries(memo) {
  const entries = [];
  let depth = 0;
  let current = '';
  for (const c of memo.trim().replace(/^\[|\]$/g, '')) {
    if ('([{'.includes(c)) depth++;
    if (')]}'.includes(c)) depth--;
    if (c === ',' && depth === 0) {
      entries.push(current);
      current = '';
    } else current += c;
  }
  entries.push(current);
  return entries.map((entry) => entry.replace(/\s+/g, ' ').trim());
}

// ── What a memoized row reads ──────────────────────────────────────────────
//
// v-memo skips a row while its array is unchanged, so anything else the row
// draws from — its own props, its menu's, the drag wrapper's `:class` and
// `:style` — is frozen on its last value. The memo must therefore name every
// reactive value those bindings read: the binding's own reads, and, for a call
// the memo does not list whole, the reads of the function it calls, followed
// into the view's script, the composable that returned it, or the store.

const KEYWORDS = new Set(['true', 'false', 'null', 'undefined', 'NaN', 'Infinity', 'if', 'else', 'return',
  'const', 'let', 'var', 'function', 'new', 'typeof', 'instanceof', 'in', 'of', 'this', 'await', 'async',
  'void', 'delete', 'Math', 'Number', 'String', 'Boolean', 'Object', 'Array', 'JSON', 'Date']);
const REACTIVE = /^(?:ref|shallowRef|computed|reactive|shallowReactive|toRef|inject|use\w+|defineProps)\s*\(/;

const escapeRe = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

/** Code with its literals blanked (a template literal keeps its `${}`) and object keys dropped. */
function codeOnly(code) {
  return code
    .replace(/`(?:[^`\\]|\\.)*`/g, (lit) => [...lit.matchAll(/\$\{([^}]*)\}/g)].map((m) => ` ${m[1]} `).join(''))
    .replace(/'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*"/g, "''")
    .replace(/([{,]\s*)[\w$]+\s*:(?!:)/g, '$1');
}

/** Every `a.b.c` an expression reads, `.value` and `?.` folded, and whether it is called. */
function reads(code) {
  return [...codeOnly(code).matchAll(/(?<![\w$.])([A-Za-z_$][\w$]*(?:\s*\??\.\s*[A-Za-z_$][\w$]*)*)(\s*\()?/g)]
    .map(([, path, call]) => {
      const clean = path.replace(/\s/g, '').replace(/\?\./g, '.').replace(/\.value(?=\.|$)/g, '');
      return { path: clean, root: clean.split('.')[0], call: Boolean(call) };
    })
    .filter(({ root }) => !KEYWORDS.has(root) && !root.startsWith('$'));
}

/** From `open` (an opening bracket), the text up to its match. */
function balanced(text, open) {
  const pair = { '(': ')', '{': '}', '[': ']' }[text[open]];
  let depth = 0;
  for (let i = open; i < text.length; i++) {
    if (text[i] === text[open]) depth++;
    else if (text[i] === pair && --depth === 0) return text.slice(open, i + 1);
  }
  throw new Error(`unbalanced ${text[open]} at ${open}`);
}

/** A module's top-level names, which of them are reactive, and each one's definition. */
function moduleScope(code) {
  const imports = new Map();
  for (const [, names, from] of code.matchAll(/import\s+([^;]+?)\s+from\s+'([^']+)'/g)) {
    for (const name of names.replace(/[{}]/g, '').split(',')) {
      const local = name.trim().split(/\s+as\s+/).pop();
      if (local) imports.set(local, from);
    }
  }
  const reactive = new Set();
  const defs = new Map();
  const destructured = new Map(); // local name → { key, factory }
  for (const match of code.matchAll(/function\s+([\w$]+)\s*\(/g)) {
    const params = balanced(code, match.index + match[0].length - 1);
    const start = code.indexOf('{', match.index + match[0].length - 1 + params.length);
    defs.set(match[1], balanced(code, start));
  }
  for (const match of code.matchAll(/(?:const|let)\s+([\w$]+)\s*=\s*/g)) {
    const rest = code.slice(match.index + match[0].length);
    if (REACTIVE.test(rest)) reactive.add(match[1]);
    const arrow = /^(?:async\s*)?(?:\([^)]*\)|[\w$]+)\s*=>\s*/.exec(rest);
    if (arrow && rest[arrow[0].length] === '{') {
      defs.set(match[1], balanced(rest, arrow[0].length));
    } else {
      let depth = 0;
      let end = 0;
      for (; end < rest.length; end++) {
        if ('([{'.includes(rest[end])) depth++;
        else if (')]}'.includes(rest[end])) depth--;
        else if (rest[end] === ';' && depth === 0) break;
      }
      defs.set(match[1], rest.slice(0, end));
    }
  }
  for (const match of code.matchAll(/const\s*\{([^}]*)\}\s*=\s*(use\w+)\s*\(/g)) {
    for (const entry of match[1].split(',')) {
      const [key, local = key] = entry.split(':').map((part) => part.trim());
      if (!key) continue;
      reactive.add(local);
      destructured.set(local, { key, factory: match[2] });
    }
  }
  return { imports, reactive, defs, destructured };
}

const SCOPES = new Map();
/** The scope of the module an `@/…` import names. */
function importedScope(from) {
  if (!SCOPES.has(from)) SCOPES.set(from, moduleScope(stripComments(readFileSync(`${SRC}/${from.slice(2)}.js`, 'utf8'))));
  return SCOPES.get(from);
}

/**
 * What the memo must hold for a function body to be in step: each reactive
 * name it reads, spelled as the view sees it (`toWord`), local calls followed.
 */
function bodyNeeds(scope, body, toWord, seen = new Set()) {
  return reads(body).flatMap(({ path, root, call }) => {
    if (scope.reactive.has(root)) return [toWord(path)];
    if (call && scope.defs.has(root) && !seen.has(root)) {
      seen.add(root);
      return bodyNeeds(scope, scope.defs.get(root), toWord, seen);
    }
    return [];
  });
}

/** What the memo must hold for a binding of a row, in the view's own words. */
function bindingNeeds(view, expression, loopVars, memoHas) {
  const needs = [];
  for (const { path, root, call } of reads(expression)) {
    if (loopVars.includes(root)) {
      needs.push(root);
      continue;
    }
    if (call && memoHas(`${path}(`)) continue;
    if (view.imports.has(root) && path === root) continue;
    if (!call) {
      // A view constant never moves; a prop or a reactive value does.
      const constant = view.defs.has(root) && !view.reactive.has(root);
      if (!view.imports.has(root) && !constant) needs.push(path);
      continue;
    }
    // A call the memo does not list whole: what does it read?
    const member = path.slice(root.length + 1);
    const factory = view.defs.get(root)?.match(/^(use\w+)\s*\(/)?.[1];
    if (member && factory && view.imports.get(factory)?.startsWith('@/stores/')) {
      const store = importedScope(view.imports.get(factory));
      needs.push(...bodyNeeds(store, store.defs.get(member) ?? '', (word) => `${root}.${word}`));
    } else if (view.destructured.has(root)) {
      const { key, factory: from } = view.destructured.get(root);
      const composable = importedScope(view.imports.get(from));
      // A name the composable returns, as the view destructured it; one it
      // keeps to itself cannot be memoized on at all.
      const returned = new Map([...view.destructured].filter(([, d]) => d.factory === from).map(([local, d]) => [d.key, local]));
      needs.push(...bodyNeeds(composable, composable.defs.get(key) ?? '', (word) => {
        const [name, ...rest] = word.split('.');
        return [returned.get(name) ?? `${from}'s own ${name}`, ...rest].join('.');
      }));
    } else if (view.defs.has(root)) {
      needs.push(...bodyNeeds(view, view.defs.get(root), (word) => word.replace(/^props\./, '')));
    } else {
      needs.push(`${path}( (unresolved)`);
    }
  }
  return needs;
}

/** Every binding a repeated row draws from: the repeated element's and everything inside it. */
function rowBindings(loop) {
  const own = Object.entries(loop.attrs).filter(([name]) => name.startsWith(':') && name !== ':key').map(([, value]) => value);
  const inner = [...loop.body.matchAll(/\s(?::[\w.-]+|v-bind:[\w.-]+|v-if|v-else-if|v-show)="([^"]*)"/g)]
    .filter(([attr]) => !/^\s:key=/.test(attr))
    .map(([, value]) => value);
  const mustaches = [...loop.body.matchAll(/\{\{([\s\S]*?)\}\}/g)].map(([, value]) => value);
  return [...own, ...inner, ...mustaches];
}

// Rows that repeat and fill `#menu`: the ones compiled with DYNAMIC_SLOTS.
const MENU_ROWS = ROWS.filter(({ loop, body }) => loop && body.includes('#menu'));

describe('track rows', () => {
  it('finds the track lists', () => {
    // A scanner that matched nothing would make the rules below vacuous.
    expect(ROWS.length).toBeGreaterThan(8);
    expect(MENU_ROWS.filter((row) => !row.wrapped).length).toBeGreaterThan(5);
    // The two windowed drag lists, repeated by a wrapper rather than the row.
    expect(MENU_ROWS.filter((row) => row.wrapped).length).toBeGreaterThanOrEqual(2);
  });

  it('hands `playing` to the current row only', () => {
    const ungated = ROWS
      .filter(({ attrs }) => ':playing' in attrs)
      .filter(({ attrs }) => !attrs[':playing'].startsWith(`${attrs[':current']} &&`))
      .map(({ file, attrs }) => `${file}: :playing="${attrs[':playing']}"`);

    expect(ungated).toEqual([]);
  });

  it('memoizes a row whose menu slot closes over the loop variable', () => {
    const unmemoized = MENU_ROWS
      .filter(({ file, loop }) => !('v-memo' in loop.attrs) && !UNMEMOIZABLE[file])
      .map(({ file, loop }) => `${file}: v-for="${loop.attrs['v-for']}"`);

    expect(unmemoized).toEqual([]);
  });

  it('keeps a memoized row in step with what it shows', () => {
    // A dep left out freezes the row on its old value, silently: the loop
    // item, and the two inputs a play/pause or a track change moves.
    const stale = MENU_ROWS
      .filter(({ loop }) => 'v-memo' in loop.attrs)
      .flatMap(({ file, attrs, loop }) => {
        const memo = memoEntries(loop.attrs['v-memo']);
        const deps = [loopItem(loop.attrs['v-for']), attrs[':current'], attrs[':playing']];
        // Each its own entry: `current && playing` alone misses a track change
        // while paused, where it stays false on both rows.
        return deps
          .filter((dep) => !memo.includes(dep.replace(/\s+/g, ' ').trim()))
          .map((dep) => `${file}: v-memo lacks ${dep}`);
      });

    expect(stale).toEqual([]);
  });

  it('names in the memo every value the row and its wrapper read', () => {
    // The three deps above are the ones every row shares; a drag wrapper's
    // `:class`/`:style`, a row's `:editing`, a menu's `:removable` read more,
    // and a memo missing one freezes it: the dragged row stops following the
    // finger, the grips never show, the held row stays where it was lifted.
    const followed = [];
    const missing = MENU_ROWS
      .filter(({ loop }) => 'v-memo' in loop.attrs)
      .flatMap(({ file, script, loop, wrapped }) => {
        const memo = loop.attrs['v-memo'].replace(/\s+/g, ' ');
        const memoHas = (word) => new RegExp(`(?<![\\w$.])${escapeRe(word)}${word.endsWith('(') ? '' : '(?![\\w$])'}`).test(memo);
        const view = moduleScope(script);
        const loopVars = /^\(?\s*([\w$]+)(?:\s*,\s*([\w$]+))?/.exec(loop.attrs['v-for']).slice(1).filter(Boolean);
        const bindings = rowBindings(loop);
        const needs = [...new Set(bindings.flatMap((binding) => bindingNeeds(view, binding, loopVars, memoHas)))];
        // A drag wrapper's style is a call into its composable: a resolver that
        // stopped at the call would find nothing to require and pass on it.
        if (wrapped) followed.push(needs.some((word) => !bindings.join(' ').includes(word)));
        return needs.filter((word) => !memoHas(word)).map((word) => `${file}: v-memo lacks ${word}`);
      });

    expect(followed.length).toBeGreaterThanOrEqual(2);
    expect(followed.every(Boolean)).toBe(true);
    expect(missing).toEqual([]);
  });

  it('exempts a row only for the reason it states', () => {
    // An exemption that outlived its reason would excuse a missing v-memo.
    const stillNested = Object.keys(UNMEMOIZABLE).filter((file) =>
      MENU_ROWS.some((row) => row.file === file && row.nested));

    expect(stillNested).toEqual(Object.keys(UNMEMOIZABLE));
  });
});
