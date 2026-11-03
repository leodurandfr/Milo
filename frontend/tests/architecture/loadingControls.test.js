// frontend/tests/architecture/loadingControls.test.js
/**
 * A component that takes `isLoading` draws its controls under a shimmer, at
 * opacity 0 but still under the finger. Its RangeSliders carry
 * `:disabled="isLoading"`; a Toggle must too. Toggle is not controlled — its
 * input binds `:checked` one way — so a tap the parent drops while loading
 * leaves the native box flipped, and when loading ends `modelValue` has not
 * moved, Vue never repatches `checked`, and the switch shows a playing zone as
 * muted. The next tap then sends the opposite of what it shows.
 *
 * What breaks: MultiroomControl's rows (MultiroomItem) during the multiroom
 * enabling transition and the registry's first load. Mounts nothing: it reads
 * the sources.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, relative, resolve } from 'node:path';
import { stripComments } from '../helpers/stripComments';

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = resolve(HERE, '../../src');
const MULTIROOM_ITEM = 'components/multiroom/MultiroomItem.vue';
/** `isLoading`, alone or as one operand of an `||` — never negated. */
const DISABLED_WHILE_LOADING = /:disabled="(?:[^"]*\|\|\s*)?isLoading(?:\s*\|\|[^"]*)?"/;

function vueFiles(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return vueFiles(path);
    return entry.name.endsWith('.vue') ? [path] : [];
  });
}

/** The text between `defineProps(` and its matching `)`, or ''. */
function propsDeclaration(script) {
  const start = script.indexOf('defineProps(');
  if (start === -1) return '';
  let depth = 0;
  for (let i = start + 'defineProps'.length; i < script.length; i += 1) {
    if (script[i] === '(') depth += 1;
    if (script[i] === ')') {
      depth -= 1;
      if (depth === 0) return script.slice(start, i + 1);
    }
  }
  return '';
}

/**
 * Every `<Toggle …>` opening tag of the template, attributes included. The scan
 * skips quoted values, since a handler like `(enabled) => …` holds a `>`.
 */
function toggleTags(template) {
  const tags = [];
  for (const match of template.matchAll(/<Toggle(?=[\s/>])/g)) {
    let quote = null;
    for (let i = match.index; i < template.length; i += 1) {
      const char = template[i];
      if (quote) {
        if (char === quote) quote = null;
      } else if (char === '"' || char === "'") {
        quote = char;
      } else if (char === '>') {
        tags.push(template.slice(match.index, i + 1));
        break;
      }
    }
  }
  return tags;
}

function loadingComponents() {
  return vueFiles(SRC).flatMap((file) => {
    const source = readFileSync(file, 'utf8');
    const script = [...source.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map((m) => m[1]).join('\n');
    if (!/\bisLoading\s*:/.test(propsDeclaration(stripComments(script)))) return [];
    const template = /<template>([\s\S]*)<\/template>/.exec(source)?.[1] ?? '';
    return [{ file: relative(SRC, file), toggles: toggleTags(template.replace(/<!--[\s\S]*?-->/g, '')) }];
  });
}

describe('controls drawn under a loading shimmer', () => {
  const components = loadingComponents();

  it('finds the components it rules on', () => {
    expect(components.length).toBeGreaterThan(0);
    const item = components.find((c) => c.file === MULTIROOM_ITEM);
    expect(item, `${MULTIROOM_ITEM} no longer declares isLoading`).toBeDefined();
    expect(item.toggles.length).toBeGreaterThanOrEqual(2);
  });

  it('every Toggle in a component that takes isLoading is disabled while it loads', () => {
    const offenders = components.flatMap(({ file, toggles }) => toggles
      .filter((tag) => !DISABLED_WHILE_LOADING.test(tag))
      .map((tag) => `${file}: ${tag.replace(/\s+/g, ' ')}`));
    expect(offenders).toEqual([]);
  });
});
