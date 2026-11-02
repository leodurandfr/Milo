// frontend/tests/architecture/resyncStores.test.js
/**
 * Structural guardrail over what App.vue's resyncStores() heals.
 *
 * The rule is written in App.vue:
 *
 *   "Stores whose WS-fed state is delta-based: events missed while disconnected
 *    or backgrounded leave them stale until refetched. Each exposes a uniform
 *    resync(); a new delta-based store MUST implement resync() and be listed
 *    here."
 *
 * The failure is invisible: state left out simply serves stale data after a
 * reconnect or a backgrounded tab, with no error anywhere. Listing the store is
 * not enough — a store can be listed while one of its refs is written by a delta
 * and by nothing its resync() calls, which is how the screen's sleep state went
 * stale with every check here green. So the rule is held per ref: every ref an
 * App.vue WS handler writes must also be written on its store's resync() path,
 * or sit in DELTA_ONLY with its reason, and a handler a composable holds must be
 * healed by a serverSync entry calling into the same module. Read from source on
 * both sides; nothing is mocked.
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

/** The body of a store's `async function resync()`, comments out. */
function resyncBody(file) {
  const source = readFileSync(join(STORES_DIR, file), 'utf8');
  const match = /\n  async function resync\(\) \{\n([\s\S]*?)\n  \}\n/.exec(source);
  return match ? match[1].replace(/\/\/.*$/gm, '') : null;
}

describe('every resync() says whether it reached the server', () => {
  it('extracts a resync() body from every store exposing one', () => {
    const missing = resyncCapable.filter(file => resyncBody(file) === null);
    expect(missing).toEqual([]);
  });

  it('answers on every path, with a value', () => {
    // serverSync retries only a domain whose resync() did not resolve true. One
    // that falls off its end, or returns bare, resolves undefined: read as a
    // failure forever, it would be refetched every 15 s for the life of the page.
    const silent = resyncCapable.filter((file) => {
      const body = resyncBody(file);
      const lines = body.split('\n').map(line => line.trim()).filter(Boolean);
      return /\breturn\s*;/.test(body) || !lines.at(-1).startsWith('return ');
    });
    expect(silent).toEqual([]);
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
    expect(boot).toContain('primeFromCache');
  });

  it('boot loads the stores by calling resyncStores()', () => {
    expect(boot).toMatch(/await resyncStores\(/);
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
    // for the inline callbacks, 1 for the named handlers), so losing any one
    // of the three fails here instead of shrinking the rule below in silence.
    expect(storesInDispatchTables(appSource).size).toBeGreaterThan(5);
    expect(storesInInlineHandlers(appSource).size).toBeGreaterThan(3);
    expect(storesInNamedTableHandlers(appSource).size).toBeGreaterThan(0);
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

/**
 * `source` with its comments dropped, so prose never reads as code. One pass
 * over strings and both comment forms, since each can hold the other's opener
 * (`// /api/x/* …`).
 */
function withoutComments(source) {
  let out = '';
  for (let index = 0; index < source.length; index += 1) {
    const char = source[index];
    if (char === '\'' || char === '"' || char === '`') {
      const start = index;
      for (index += 1; index < source.length && source[index] !== char; index += 1) {
        if (source[index] === '\\') index += 1;
      }
      out += source.slice(start, index + 1);
    } else if (char === '/' && source[index + 1] === '/') {
      while (index < source.length && source[index] !== '\n') index += 1;
      out += '\n';
    } else if (char === '/' && source[index + 1] === '*') {
      const end = source.indexOf('*/', index + 2);
      if (end === -1) throw new Error(`unterminated comment at ${index} — the extractor is broken`);
      index = end + 1;
    } else {
      out += char;
    }
  }
  return out;
}

/** Index of the parenthesis closing the one at `open`, string literals skipped. */
function closingParen(source, open) {
  let depth = 0;
  for (let index = open; index < source.length; index += 1) {
    const char = source[index];
    if (char === '\'' || char === '"' || char === '`') {
      for (index += 1; index < source.length && source[index] !== char; index += 1) {
        if (source[index] === '\\') index += 1;
      }
    } else if (char === '(') {
      depth += 1;
    } else if (char === ')') {
      depth -= 1;
      if (depth === 0) return index;
    }
  }
  throw new Error(`unbalanced parenthesis at ${open} — the extractor is broken`);
}

/** `text` split at its top-level commas. */
function splitTopLevel(text) {
  const parts = [];
  let depth = 0;
  let current = '';
  for (const char of text) {
    if ('([{'.includes(char)) depth += 1;
    else if (')]}'.includes(char)) depth -= 1;
    if (char === ',' && depth === 0) {
      parts.push(current.trim());
      current = '';
    } else {
      current += char;
    }
  }
  if (current.trim()) parts.push(current.trim());
  return parts;
}

/** The arguments of the call whose parenthesis opens at `open`. */
function callArguments(source, open) {
  return splitTopLevel(source.slice(open + 1, closingParen(source, open)));
}

/** Every `xStore.method(` App.vue's WS subscriptions call, and the table handlers it does not define. */
function wsHandlerCalls(source) {
  const calls = new Set();
  const composableHandlers = [];
  const addCalls = (text) => {
    for (const [, store, method] of text.matchAll(/(\w+Store)\.(\w+)\(/g)) calls.add(`${store}.${method}`);
  };
  for (const name of DISPATCH_TABLES) {
    for (const handler of tableHandlers(source, name)) {
      if (handler.includes('.')) {
        calls.add(handler);
        continue;
      }
      const body = new RegExp(`function ${handler}\\s*\\([^)]*\\)\\s*\\{([\\s\\S]*?)\\n\\}`).exec(source);
      if (body) addCalls(body[1]);
      else composableHandlers.push(handler);
    }
  }
  for (const match of source.matchAll(/(?<![.\w])(parsedOn|on)\(/g)) {
    const open = match.index + match[0].length - 1;
    addCalls(source.slice(open, closingParen(source, open)));
  }
  return { calls: [...calls], composableHandlers };
}

/**
 * A store module read as functions: its top-level `function`s and arrow
 * consts, and every `const h = factory(…)` as a function whose body is that
 * call, so a factory's writes reach the refs it was handed.
 */
function storeModule(file) {
  const source = withoutComments(readFileSync(join(STORES_DIR, file), 'utf8'));
  const functions = new Map();
  // A positional parameter by its name; a destructured one only shadows.
  const add = (name, params, body) => {
    const positional = [];
    const locals = new Set();
    for (const param of splitTopLevel(params)) {
      if (/^[{[]/.test(param)) {
        for (const [local] of param.matchAll(/\w+(?=\s*[,}\]=]|\s*$)/g)) locals.add(local);
        positional.push(null);
      } else {
        positional.push(/\w+/.exec(param)[0]);
      }
    }
    functions.set(name, { params: positional, locals, body });
  };
  for (const [, name, params, body] of source.matchAll(
    /\n  (?:async )?function (\w+)\s*\(([^)]*)\)\s*\{([\s\S]*?)\n  \}/g)) add(name, params, body);
  for (const [, name, params, body] of source.matchAll(
    /\n  const (\w+) = (?:async )?\(([^)]*)\) => \{([\s\S]*?)\n  \};/g)) add(name, params, body);
  for (const match of source.matchAll(/\n  const (\w+) = (\w+)\(/g)) {
    const [, name, factory] = match;
    const open = match.index + match[0].length - 1;
    if (!functions.has(name)) add(name, '', `${factory}${source.slice(open, closingParen(source, open) + 1)}`);
  }
  const refs = new Set(
    [...source.matchAll(/\n  const (\w+) = (?:ref|shallowRef|reactive)\(/g)].map(([, name]) => name));
  return { functions, refs };
}

const WRITE_PATTERNS = [
  /(?<![.\w])(\w+)\.value((?:\.\w+|\[[^\]]+\])*)\s*(?:=(?![=>])|\+=|-=|\+\+|--)/g,
  /(?<![.\w])(\w+)\.value((?:\.\w+|\[[^\]]+\])*)\.(?:set|add|delete|clear|push|pop|shift|unshift|splice)\(/g,
  /\bdelete (\w+)\.value((?:\.\w+|\[[^\]]+\])*)/g,
  /\bObject\.assign\((\w+)\.value((?:\.\w+|\[[^\]]+\])*)\s*,/g,
];

/**
 * What `name` writes, as `ident`, `ident.prop` or `ident.[]` (one entry of a
 * keyed ref), following every call to a
 * function of the same module. A write to the function's own i-th parameter
 * comes back as `#i`, which its caller maps onto the identifier it passed —
 * how `makeUpdater(volumeSteps)` and `setIfChanged(daylightLocation, …)` reach
 * the ref they were handed. The caller filters on the module's refs.
 */
function writesOf(module, name, stack = new Set()) {
  const fn = module.functions.get(name);
  if (!fn || stack.has(name)) return new Set();
  stack.add(name);
  const out = new Set();
  const record = (ident, prop) => {
    if (fn.locals.has(ident)) return;
    const position = fn.params.indexOf(ident);
    const target = position === -1 ? ident : `#${position}`;
    out.add(prop ? `${target}.${prop}` : target);
  };
  for (const pattern of WRITE_PATTERNS) {
    for (const match of fn.body.matchAll(pattern)) {
      const [, ident, chain] = match;
      const merge = !chain && new RegExp(`^\\s*\\{\\s*\\.\\.\\.${ident}\\.value\\b`)
        .test(fn.body.slice(match.index + match[0].length));
      const step = /^(?:\.(\w+)|(\[))/.exec(chain);
      record(ident, merge ? '*' : step?.[1] ?? (step?.[2] && '[]'));
    }
  }
  for (const match of fn.body.matchAll(/(?<![.\w$])(\w+)\(/g)) {
    if (!module.functions.has(match[1]) || match[1] === name) continue;
    const args = callArguments(fn.body, match.index + match[0].length - 1);
    for (const write of writesOf(module, match[1], new Set(stack))) {
      const [ident, prop] = write.split('.');
      if (!ident.startsWith('#')) out.add(write);
      else if (/^\w+$/.test(args[Number(ident.slice(1))] ?? '')) record(args[Number(ident.slice(1))], prop);
    }
  }
  return out;
}

/** writesOf(), kept to the refs the module declares. */
function refWritesOf(module, name) {
  return new Set([...writesOf(module, name)].filter(write => module.refs.has(write.split('.')[0])));
}

/**
 * `ref.prop` and `ref.[]` are healed by a resync writing the same or the whole
 * ref, `ref` only by the whole ref, and `ref.*` — a `{ ...ref.value, ...patch }`
 * merge, whose keys are the payload's — by the whole ref or any of its
 * properties.
 */
function healedBy(write, resyncWrites) {
  const [ident, prop] = write.split('.');
  if (resyncWrites.has(write) || resyncWrites.has(ident)) return true;
  return prop === '*' && [...resyncWrites].some(other => other.startsWith(`${ident}.`));
}

const storeKey = binding => binding.replace(/Store$/, '');

const SEARCH_VIEW = 'the search view of the user\'s last query, refetched by the next search; '
  + 'the favorites and custom stations an event touches are healed by resync()';
const COMPLETED_MARK = 'a "just finished" mark bridging one stale frame until the inventory refetch '
  + 'it triggers lands; a completion missed while away needs no mark, resync() reloads that inventory';
/**
 * Refs a WS handler writes that no resync() path writes, each with why that is
 * right. An entry must name state the server has no read of, or one a resync
 * heals by another road; a fetch the delta could have been missed for is a
 * missing resync, not an entry.
 */
const DELTA_ONLY = new Map([
  ['podcast.progressCache', 'the "listened" mark written at an episode\'s end; '
    + 'the episode views re-read playback_progress from the server as they load'],
  ['podcast.latestSubscriptionEpisodes', 'derived from the subscriptions: resync() clears '
    + 'subscriptionsLoaded, so the next home or subscriptions view refetches it'],
  ['multiroom.transitionState', 'a toggle in flight, which the server offers no read of; '
    + 'a missed end is released by the transition timeout or resetTransition()'],
  ['multiroom.transitionError', 'set and cleared with transitionState'],
  ['multiroom.configuringClients', 'this tab\'s own configure request in flight, set by the click '
    + 'and cleared by the event or its timeout; the server offers no read of it'],
  ['equalizer.outputPeak', 'a live meter, overwritten by the next levels event'],
  ['radio.loading', SEARCH_VIEW],
  ['radio.hasError', SEARCH_VIEW],
  ['radio.searchResults', SEARCH_VIEW],
  ['radio.displayedCount', SEARCH_VIEW],
  ['radio.searchUnavailable', SEARCH_VIEW],
  ['radio.topStationsCache', SEARCH_VIEW],
  ['radio.topStationsCacheTimestamp', SEARCH_VIEW],
  ['updates.satelliteCompletedUpdates', COMPLETED_MARK],
  ['updates.satelliteAppCompletedUpdates', COMPLETED_MARK],
  ['updates.satelliteCamillaCompletedUpdates', COMPLETED_MARK],
]);

/** App.vue's script, comments out: an apostrophe in prose would derail the paren matcher. */
const appScript = withoutComments(/<script setup>([\s\S]*?)<\/script>/.exec(appSource)[1]);
const handlerSurface = wsHandlerCalls(appScript);
const modules = new Map();
function moduleOf(binding) {
  const file = bindings.get(binding);
  if (!modules.has(file)) modules.set(file, storeModule(file));
  return modules.get(file);
}

describe('WS-written refs ↔ resync paths', () => {
  const storeCalls = handlerSurface.calls
    .map(call => call.split('.'))
    .filter(([binding]) => bindings.has(binding));

  it('extracts a plausible handler surface', () => {
    expect(storeCalls.length).toBeGreaterThan(40);
    expect(new Set(storeCalls.map(([binding]) => binding)).size).toBeGreaterThan(8);
  });

  it('resolves every WS handler to a function of its store', () => {
    // An unresolved method is a handler whose writes this guardrail cannot see.
    const unbound = handlerSurface.calls.filter(call => !bindings.has(call.split('.')[0]));
    const unresolved = storeCalls
      .filter(([binding, method]) => !moduleOf(binding).functions.has(method))
      .map(call => call.join('.'));
    expect([...unbound, ...unresolved]).toEqual([]);
  });

  it('finds a ref written by every handler and by every resync() it is held to', () => {
    // A handler or a resync() read as writing nothing makes the rule below
    // pass on an empty set — the closure lost a call or a write shape.
    const blind = storeCalls
      .filter(([binding, method]) => refWritesOf(moduleOf(binding), method).size === 0)
      .map(call => call.join('.'));
    const blindResync = [...new Set(storeCalls.map(([binding]) => binding))]
      .filter(binding => refWritesOf(moduleOf(binding), 'resync').size === 0);
    expect(blind).toEqual([]);
    expect(blindResync).toEqual([]);
  });

  const unhealedWrites = () => {
    const unhealed = new Set();
    for (const [binding, method] of storeCalls) {
      const module = moduleOf(binding);
      const resyncWrites = refWritesOf(module, 'resync');
      for (const write of refWritesOf(module, method)) {
        if (!healedBy(write, resyncWrites)) unhealed.add(`${storeKey(binding)}.${write.split('.')[0]}`);
      }
    }
    return unhealed;
  };

  it('every ref an App.vue WS handler writes is also written on its store\'s resync() path', () => {
    // A delta missed while the tab was backgrounded or the socket down is
    // never replayed: what only a delta writes stays stale until a reload.
    const unhealed = [...unhealedWrites()].filter(key => !DELTA_ONLY.has(key));
    expect(unhealed).toEqual([]);
  });

  it('every DELTA_ONLY entry is still a WS-written ref resync() leaves alone', () => {
    const unhealed = unhealedWrites();
    expect([...DELTA_ONLY.keys()].filter(key => !unhealed.has(key))).toEqual([]);
  });
});

/** The function names `createServerSync([...])`'s entries call as `resync: () => f(`. */
function serverSyncResyncCalls(source) {
  const start = source.indexOf('createServerSync([');
  if (start === -1) throw new Error('createServerSync([ not found in App.vue — the extractor is broken');
  const open = source.indexOf('(', start);
  const block = source.slice(open, closingParen(source, open));
  return [...block.matchAll(/resync:\s*\(\)\s*=>\s*(\w+)\(/g)].map(([, name]) => name);
}

/** `import { a, b } from '@/composables/X'` → a: 'X', b: 'X'. */
function composableImports(source) {
  const imports = new Map();
  for (const [, names, module] of source.matchAll(/import\s*\{([^}]*)\}\s*from\s*'@\/composables\/(\w+)'/g)) {
    for (const name of names.split(',').map(n => n.trim().split(/\s+as\s+/).at(-1)).filter(Boolean)) {
      imports.set(name, module);
    }
  }
  return imports;
}

describe('composable-held WS state ↔ serverSync', () => {
  const imports = composableImports(appScript);
  const healedModules = new Set(serverSyncResyncCalls(appScript).map(name => imports.get(name)).filter(Boolean));

  it('extracts a plausible surface', () => {
    expect(handlerSurface.composableHandlers.length).toBeGreaterThan(0);
    expect(serverSyncResyncCalls(appScript).length).toBeGreaterThan(0);
  });

  it('every table handler App.vue does not define is imported from a composable', () => {
    const unresolved = handlerSurface.composableHandlers.filter(handler => !imports.has(handler));
    expect(unresolved).toEqual([]);
  });

  it('every composable-held WS state is healed by serverSync', () => {
    // A composable's module-level state is in no store's resync(); its deltas
    // are just as missable, so serverSync must call into the same module.
    const unhealed = handlerSurface.composableHandlers
      .filter(handler => !healedModules.has(imports.get(handler)));
    expect(unhealed).toEqual([]);
  });
});
