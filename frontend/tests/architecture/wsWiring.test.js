// frontend/tests/architecture/wsWiring.test.js
/**
 * Structural guardrail over the WebSocket dispatch layer. Two rules that are
 * written down but were, until now, enforced by nobody:
 *
 *   1. "Handlers are registered centrally in App.vue and dispatch into Pinia
 *      stores — components react to store state, never to raw events."
 *      (services/websocket.js header, CLAUDE.md § Frontend conventions)
 *   2. Every schema in the wsEventRegistry has a live parsedOn() consumer, and
 *      every parsedOn() names a schema the registry actually defines.
 *
 * Rule 2 is the one with a track record: `multiroom.crossover_changed` kept a
 * schema, a subscription and a handler for a payload the UI never read, because
 * nothing tied the three together. A schema outliving its consumer is dead
 * weight that reads as a contract; a parsedOn() naming a missing key silently
 * passes `undefined` as the schema and blows up at runtime on the first event.
 *
 * Both halves are extracted from source, and each extraction asserts it found a
 * plausible surface first — a broken parse must fail loudly, not pass on an
 * empty set.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC_DIR = resolve(HERE, '../../src');
const APP_VUE = join(SRC_DIR, 'App.vue');
const WS_SCHEMAS = join(SRC_DIR, 'schemas/ws.js');

const appSource = readFileSync(APP_VUE, 'utf8');

/** Every file under src/ except the two that legitimately own WS wiring. */
function sourceFilesExceptWsLayer() {
  const files = [];
  const walk = (dir) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const full = join(dir, entry.name);
      if (entry.isDirectory()) walk(full);
      else if (/\.(js|vue)$/.test(entry.name)) files.push(full);
    }
  };
  walk(SRC_DIR);
  return files.filter(f => f !== APP_VUE && f !== join(SRC_DIR, 'services/websocket.js'));
}

/** The `'category.type'` keys declared in `export const wsEventRegistry = {...}`. */
function registryKeys() {
  const source = readFileSync(WS_SCHEMAS, 'utf8');
  const block = /export const wsEventRegistry = \{([\s\S]*)\n\};/.exec(source);
  if (!block) throw new Error('wsEventRegistry not found in schemas/ws.js — the extractor is broken');
  return [...block[1].matchAll(/^\s{2}'([\w]+\.[\w]+)':/gm)].map(m => m[1]);
}

/** The `['category', 'type', handler]` rows of one of App.vue's dispatch tables. */
function tableRows(name) {
  const block = new RegExp(`const ${name} = \\[([\\s\\S]*?)\\n\\];`).exec(appSource);
  if (!block) throw new Error(`${name} not found in App.vue — the extractor is broken`);
  return [...block[1].matchAll(/\['(\w+)',\s*'(\w+)'/g)].map(m => [m[1], m[2]]);
}

/**
 * Every Zod-validated subscription, as `[pair, schemaKey]`.
 *
 * Two sources, because both shapes are legal: PARSED_EVENTS rows, which derive
 * the schema from their own pair, and any inline parsedOn() left in the file
 * with a literal registry key.
 */
function parsedSubscriptions() {
  const fromTable = tableRows('PARSED_EVENTS').map(([c, t]) => [`${c}.${t}`, `${c}.${t}`]);
  const fromInline = [...appSource.matchAll(
    /parsedOn\(\s*'([^']+)'\s*,\s*'([^']+)'\s*,\s*wsEventRegistry\['([^']+)'\]/g,
  )].map(m => [`${m[1]}.${m[2]}`, m[3]]);
  return [...fromTable, ...fromInline];
}

describe('WS event handlers live in App.vue, not in components', () => {
  const files = sourceFilesExceptWsLayer();

  it('scans a plausible number of source files', () => {
    expect(files.length).toBeGreaterThan(100);
  });

  it('no module outside the WS layer destructures on()/parsedOn()', () => {
    // useWebSocket also exposes onReconnect/onVisibilityChange — lifecycle
    // callbacks, not event handlers. A component may legitimately hook those
    // to refetch data only it displays (UpdateManager does, for satellites).
    // What it must not do is subscribe to an event: that handler dies with the
    // component, so events arriving while it is unmounted are lost with
    // nothing to heal them, and the state it fed goes quietly stale.
    const offenders = files
      .filter((file) => {
        const source = readFileSync(file, 'utf8');
        if (!/from\s+'@\/services\/websocket'/.test(source)) return false;
        return [...source.matchAll(/const\s*\{([^}]*)\}\s*=\s*useWebSocket\(\)/g)]
          .some(([, bound]) =>
            bound.split(',').map(n => n.trim()).some(n => n === 'on' || n === 'parsedOn')
          );
      })
      .map(file => file.slice(SRC_DIR.length + 1));

    expect(offenders).toEqual([]);
  });
});

/** `const radioStore = useRadioStore()` → radioStore: 'radioStore.js' */
function storeBindings() {
  const bindings = new Map();
  for (const [, local, name] of appSource.matchAll(/const\s+(\w+)\s*=\s*use(\w+Store)\(\)/g)) {
    bindings.set(local, `${name[0].toLowerCase()}${name.slice(1)}.js`);
  }
  return bindings;
}

/** The names a store module returns from its setup function. */
function storeExports(file) {
  const source = readFileSync(join(SRC_DIR, 'stores', file), 'utf8');
  const returnBlock = /\n  return\s*\{([\s\S]*?)\n  \};/.exec(source);
  if (!returnBlock) throw new Error(`no return block in ${file} — the extractor is broken`);
  return returnBlock[1]
    .split(',')
    .map(entry => entry.replace(/\/\/.*$/gm, '').split(':')[0].trim())
    .filter(Boolean);
}

/** `['category', 'type', storeLocal.method]` rows across all three tables. */
function tableHandlers() {
  const rows = [];
  for (const name of ['RAW_EVENTS', 'PARSED_EVENTS', 'SETTINGS_CONFIG_EVENTS']) {
    const block = new RegExp(`const ${name} = \\[([\\s\\S]*?)\\n\\];`).exec(appSource);
    if (!block) throw new Error(`${name} not found in App.vue — the extractor is broken`);
    for (const [, handler] of block[1].matchAll(/,\s*(\w+\.\w+)\]/g)) rows.push([name, handler]);
  }
  return rows;
}

describe('App.vue dispatch tables ↔ store surfaces', () => {
  const bindings = storeBindings();
  const handlers = tableHandlers();

  it('extracts a plausible surface from both sides', () => {
    expect(bindings.size).toBeGreaterThan(5);
    expect(handlers.length).toBeGreaterThan(30);
  });

  it('every table row names a method its store actually exports', () => {
    const dangling = handlers
      .filter(([, handler]) => bindings.has(handler.split('.')[0]))
      .filter(([, handler]) => {
        const [local, method] = handler.split('.');
        return !storeExports(bindings.get(local)).includes(method);
      })
      .map(([table, handler]) => `${table}: ${handler}`);

    // A row naming a method the store does not expose subscribes `undefined`
    // as the handler: no error at boot, and a TypeError on the first event of
    // that type — which for a rarely-fired event can be months later.
    expect(dangling).toEqual([]);
  });
});

describe('wsEventRegistry ↔ App.vue parsedOn() subscriptions', () => {
  const declared = registryKeys();
  const parsed = parsedSubscriptions();
  const subscribed = parsed.map(([, key]) => key);

  it('extracts a plausible surface from both sides', () => {
    expect(declared.length).toBeGreaterThan(5);
    expect(subscribed.length).toBeGreaterThan(5);
  });

  it('every declared schema has a consumer', () => {
    const orphaned = declared.filter(key => !subscribed.includes(key));

    // A schema with no parsedOn() is a payload contract nothing enforces —
    // it reads as live coupling while the handler behind it may be long gone.
    expect(orphaned).toEqual([]);
  });

  it('every parsedOn() resolves to a declared schema', () => {
    const missing = subscribed.filter(key => !declared.includes(key));

    // wsEventRegistry['typo'] is undefined, and parsedOn() would call
    // undefined.safeParse() on the first event of that type.
    expect(missing).toEqual([]);
  });

  it('each parsedOn() passes the schema for the pair it subscribes to', () => {
    const mismatched = parsed.filter(([pair, key]) => pair !== key);

    // Subscribing to (a, b) while validating against the schema for (c, d)
    // logs a bogus validation warning and falls back to the raw payload.
    // PARSED_EVENTS makes that unrepresentable — it derives the key from the
    // row — so this now guards the inline parsedOn() shape, still legal.
    expect(mismatched).toEqual([]);
  });
});
