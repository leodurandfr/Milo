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

const DISPATCH_TABLES = ['RAW_EVENTS', 'PARSED_EVENTS', 'SETTINGS_CONFIG_EVENTS'];

/** The `[..., handler]` column of one of App.vue's dispatch tables. */
function tableHandlers(source, name) {
  const block = new RegExp(`const ${name} = \\[([\\s\\S]*?)\\n\\];`).exec(source);
  if (!block) throw new Error(`${name} not found in App.vue — the extractor is broken`);
  return [...block[1].matchAll(/,\s*([\w.]+)\]/g)].map(m => m[1]);
}

/**
 * Store identifiers referenced inside an inline `on(` / `parsedOn(` callback.
 * Walks each call to its matching paren so multi-line handler bodies are
 * covered.
 */
function storesInInlineHandlers(source) {
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

/** Store identifiers named directly in a dispatch table's handler column. */
function storesInDispatchTables(source) {
  const referenced = new Set();
  for (const name of DISPATCH_TABLES) {
    for (const handler of tableHandlers(source, name)) {
      const [local] = handler.split('.');
      if (/Store$/.test(local)) referenced.add(local);
    }
  }
  return referenced;
}

/**
 * Store identifiers reached through a table row's *named* handler — a local
 * function in App.vue rather than a store method (`handleConnectivityChanged`).
 * Third shape, third extractor: the store it mutates appears in neither of the
 * two above.
 */
function storesInNamedTableHandlers(source) {
  const referenced = new Set();
  for (const name of DISPATCH_TABLES) {
    for (const handler of tableHandlers(source, name)) {
      if (handler.includes('.')) continue;
      const body = new RegExp(`function ${handler}\\s*\\([^)]*\\)\\s*\\{([\\s\\S]*?)\\n\\}`).exec(source);
      if (!body) continue;  // imported from a composable, not a store mutator
      for (const [, identifier] of body[1].matchAll(/(\w+Store)\./g)) referenced.add(identifier);
    }
  }
  return referenced;
}

/**
 * Every store a WS handler mutates, whichever of the three shapes it takes.
 *
 * The tables are the shape that matters: `RAW_EVENTS.map(([c, t, handler]) =>
 * on(c, t, handler))` names no store inside the `on(` call, so reading the
 * subscription bodies alone saw 5 of the 10 WS-fed stores and quietly held the
 * other 5 to no rule at all.
 */
function storesMutatedByWsHandlers(source) {
  return new Set([
    ...storesInInlineHandlers(source),
    ...storesInDispatchTables(source),
    ...storesInNamedTableHandlers(source),
  ]);
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

/**
 * The async half of App.vue's onMounted — everything after the WS subscriptions
 * are registered. That block is the boot path; the subscriptions above it are
 * not, and they mention every store.
 */
function bootBlock(source) {
  const push = source.indexOf('cleanupFunctions.push(');
  if (push === -1) throw new Error('cleanupFunctions.push( not found in App.vue — the extractor is broken');
  let depth = 0;
  let index = source.indexOf('(', push);
  for (; index < source.length; index += 1) {
    if (source[index] === '(') depth += 1;
    else if (source[index] === ')') {
      depth -= 1;
      if (depth === 0) break;
    }
  }
  const end = source.indexOf('onUnmounted(', index);
  if (end === -1) throw new Error('onUnmounted( not found in App.vue — the extractor is broken');
  // Comments out: the block explains itself by naming the very calls the rules
  // below look for, and a prose mention must not read as a call site.
  return source.slice(index, end).replace(/\/\/.*$/gm, '');
}

/**
 * Store calls the boot path may make outside resyncStores(). Only one qualifies:
 * it reads localStorage to show the last known registry before any request, so
 * it describes no server state and cannot fall out of step with resync().
 */
const BOOT_ONLY_STORE_CALLS = new Set(['multiroomStore.primeFromCache']);

describe('App.vue has one recipe for populating the stores', () => {
  const boot = bootBlock(appSource);

  it('extracts a plausible boot block', () => {
    // A mis-sliced block would make the rules below pass on an empty string.
    expect(boot.length).toBeGreaterThan(200);
    expect(boot).toContain('loadHardwareInfo');
  });

  it('boot loads the stores by calling resyncStores()', () => {
    expect(boot).toMatch(/await resyncStores\(\)/);
  });

  it('populates the stores through resyncStores() and nothing else', () => {
    const direct = [...boot.matchAll(/(\w+Store)\.(\w+)\(/g)]
      .map(([, store, method]) => `${store}.${method}`)
      .filter(call => !BOOT_ONLY_STORE_CALLS.has(call));

    // A second, hand-written boot list is the failure this pass fixed: for
    // months it fetched the registry but not the pending clients, so the first
    // heartbeat of a long-known satellite read as a brand-new speaker and the
    // kiosk woke itself and opened Settings, once per page load. Both lists
    // look like they populate the app, so nothing surfaces the divergence.
    expect(direct).toEqual([]);
  });
});

describe('WS subscriptions ↔ deltaStores', () => {
  it('sees more stores than any single dispatch shape yields', () => {
    // The floor is above what each extractor finds alone (8 for the tables, 5
    // for the inline callbacks, 2 for the named handlers), so losing any one
    // of the three fails here instead of shrinking the rule below in silence.
    expect(storesInDispatchTables(appSource).size).toBeGreaterThan(5);
    expect(storesInInlineHandlers(appSource).size).toBeGreaterThan(3);
    expect(storesInNamedTableHandlers(appSource).size).toBeGreaterThan(1);
    expect(storesMutatedByWsHandlers(appSource).size).toBeGreaterThan(8);
  });

  it('every store mutated by a WS handler is delta-listed or snapshot-fed', () => {
    const mutated = storesMutatedByWsHandlers(appSource);

    const unhealed = [...mutated]
      .filter(id => bindings.has(id))
      .filter(id => !declared.includes(id));

    // Receiving WS deltas without being resynced is exactly the stale-state bug
    // the contract exists to prevent.
    expect(unhealed).toEqual([]);
  });
});
