// frontend/tests/architecture/resyncStores.test.js
/**
 * Structural guardrail over App.vue's `deltaStores` list.
 *
 * The rule is already written in App.vue:
 *
 *   "Stores whose WS-fed state is delta-based: events missed while disconnected
 *    or backgrounded leave them stale until refetched. Each exposes a uniform
 *    resync(); a new delta-based store MUST implement resync() and be listed
 *    here."
 *
 * Until now nothing enforced it, and the failure is invisible: a store left out
 * simply serves stale data after a reconnect or a backgrounded tab, with no
 * error anywhere. This test reads the source of both halves — the stores' public
 * surface and App.vue's array — and fails when they disagree.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC_DIR = resolve(HERE, '../../src');
const STORES_DIR = join(SRC_DIR, 'stores');
const APP_VUE = join(SRC_DIR, 'App.vue');

const appSource = readFileSync(APP_VUE, 'utf8');

/**
 * Stores fed by full snapshots rather than deltas: `unifiedAudioStore` is
 * rebuilt wholesale from `full_state` / `initial_state` on every reconnect, so
 * it heals without a resync().
 */
const SNAPSHOT_FED_STORES = new Set(['unifiedStore']);

/** `const radioStore = useRadioStore()` → radioStore: 'radioStore.js' */
function storeBindings(source) {
  const bindings = new Map();
  for (const [, local, name] of source.matchAll(/const\s+(\w+)\s*=\s*use(\w+Store)\(\)/g)) {
    bindings.set(local, `${name[0].toLowerCase()}${name.slice(1)}.js`);
  }
  return bindings;
}

/** The identifiers listed in `const deltaStores = [...]`. */
function deltaStoreIdentifiers(source) {
  const match = /const\s+deltaStores\s*=\s*\[([\s\S]*?)\]/.exec(source);
  if (!match) throw new Error('deltaStores array not found in App.vue — the extractor is broken');
  return match[1]
    .split(',')
    .map(entry => entry.replace(/\/\/.*$/gm, '').trim())
    .filter(Boolean);
}

/** Store modules whose public surface includes resync(). */
function storesExposingResync() {
  const exposing = [];
  for (const file of readdirSync(STORES_DIR).filter(f => f.endsWith('.js'))) {
    const source = readFileSync(join(STORES_DIR, file), 'utf8');
    const returnBlock = /\n  return\s*\{([\s\S]*?)\n  \};/.exec(source);
    if (!returnBlock) continue;
    const exported = returnBlock[1]
      .split(',')
      .map(entry => entry.replace(/\/\/.*$/gm, '').split(':')[0].trim());
    if (exported.includes('resync')) exposing.push(file);
  }
  return exposing;
}

/**
 * Store identifiers referenced inside a WS subscription callback in App.vue.
 * Walks each `on(` / `parsedOn(` call to its matching paren so multi-line
 * handler bodies are covered.
 */
function storesMutatedByWsHandlers(source) {
  const referenced = new Set();
  for (const match of source.matchAll(/(?<![.\w])(parsedOn|on)\(/g)) {
    let depth = 0;
    let index = match.index + match[0].length - 1;
    const start = index;
    for (; index < source.length; index += 1) {
      if (source[index] === '(') depth += 1;
      else if (source[index] === ')') {
        depth -= 1;
        if (depth === 0) break;
      }
    }
    for (const [, identifier] of source.slice(start, index).matchAll(/(\w+Store)\./g)) {
      referenced.add(identifier);
    }
  }
  return referenced;
}

const bindings = storeBindings(appSource);
const declared = deltaStoreIdentifiers(appSource);
const resyncCapable = storesExposingResync();

describe('App.vue deltaStores ↔ stores exposing resync()', () => {
  it('extracts a plausible surface from both sides', () => {
    // Guards the extractors: an empty parse would make every check vacuous.
    expect(bindings.size).toBeGreaterThan(5);
    expect(declared.length).toBeGreaterThan(5);
    expect(resyncCapable.length).toBeGreaterThan(5);
  });

  it('lists every store that implements resync()', () => {
    const listedFiles = declared.map(id => bindings.get(id)).filter(Boolean).sort();

    // A store implementing resync() but absent from the array is never healed:
    // its deltas missed while the tab was backgrounded are gone for good.
    expect(listedFiles).toEqual([...resyncCapable].sort());
  });

  it('lists only identifiers that resolve to a store', () => {
    const unresolved = declared.filter(id => !bindings.has(id));

    expect(unresolved).toEqual([]);
  });

  it('every listed store actually implements resync()', () => {
    const withoutResync = declared
      .map(id => bindings.get(id))
      .filter(file => file && !resyncCapable.includes(file));

    // resyncStores() calls store.resync() on each entry — a missing one throws
    // inside Promise.allSettled, silently skipping the whole heal.
    expect(withoutResync).toEqual([]);
  });
});

describe('WS subscriptions ↔ deltaStores', () => {
  it('every store mutated by a WS handler is delta-listed or snapshot-fed', () => {
    const mutated = storesMutatedByWsHandlers(appSource);
    expect(mutated.size).toBeGreaterThan(3);

    const unhealed = [...mutated]
      .filter(id => bindings.has(id))
      .filter(id => !declared.includes(id) && !SNAPSHOT_FED_STORES.has(id));

    // Receiving WS deltas without being resynced is exactly the stale-state bug
    // the contract exists to prevent.
    expect(unhealed).toEqual([]);
  });
});
