// frontend/tests/architecture/wsWiring.test.js
/**
 * Structural guardrail over the WebSocket dispatch layer. Three rules that are
 * written down but were, until now, enforced by nobody:
 *
 *   1. "Handlers are registered centrally in App.vue and dispatch into Pinia
 *      stores — components react to store state, never to raw events."
 *      (services/websocket.js header, CLAUDE.md § Frontend conventions)
 *   2. Every schema in the wsEventRegistry has a live parsedOn() consumer, and
 *      every parsedOn() names a schema the registry actually defines.
 *   3. Every (category, type) App.vue subscribes to is one the backend declares
 *      in core/models/ws_events.py.
 *
 * Rule 2 is the one with a track record: `multiroom.crossover_changed` kept a
 * schema, a subscription and a handler for a payload the UI never read, because
 * nothing tied the three together. A schema outliving its consumer is dead
 * weight that reads as a contract; a parsedOn() naming a missing key silently
 * passes `undefined` as the schema and blows up at runtime on the first event.
 *
 * Rule 3 covers the pairs no schema holds: renaming `ScreenThemeChanged.TYPE`
 * (and regenerating the reference docs) left every other test green while
 * App.vue kept listening to the old name, so a theme changed from another
 * screen never reached this one.
 *
 * Every side is extracted from source, and each extraction asserts it found a
 * plausible surface first — a broken parse must fail loudly, not pass on an
 * empty set.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';
import { parseWsEventModels } from '../helpers/wsEventModels.js';

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

/** The body of one of App.vue's `const NAME = [ … ];` dispatch tables. */
function tableBlock(name) {
  const block = new RegExp(`const ${name} = \\[([\\s\\S]*?)\\n\\];`).exec(appSource);
  if (!block) throw new Error(`${name} not found in App.vue — the extractor is broken`);
  return block[1];
}

/** The `['category', 'type', handler]` rows of one of App.vue's dispatch tables. */
function tableRows(name) {
  return [...tableBlock(name).matchAll(/\['(\w+)',\s*'(\w+)'/g)].map(m => [m[1], m[2]]);
}

/** The event types of SETTINGS_CONFIG_EVENTS, whose `[type, mutator]` rows imply `settings`. */
function settingsConfigTypes() {
  return [...tableBlock('SETTINGS_CONFIG_EVENTS').matchAll(/^\s*\['(\w+)',/gm)].map(m => m[1]);
}

const SUBSCRIBE_CALL = /\b(?:on|parsedOn)\s*\(/g;
const LITERAL_SUBSCRIBE = /^(?:on|parsedOn)\s*\(\s*'(\w+)'\s*,\s*'(\w+)'/;
// A table's subscriptions are the arrow body of `TABLE.map(([…]) => on(…))`.
const TABLE_MAP_BODY = /(\w+)\.map\(\s*\(\[[^\]]*\]\)\s*=>\s*$/;

/**
 * Every on()/parsedOn() call in App.vue, classified: a literal `('c', 't', …)`
 * pair, the body of a dispatch table's `.map()`, or neither.
 */
function subscribeCalls() {
  const sites = [...appSource.matchAll(SUBSCRIBE_CALL)].map(m => m.index);
  const literal = [];
  const tables = [];
  const unaccounted = [];
  sites.forEach((index, i) => {
    const call = LITERAL_SUBSCRIBE.exec(appSource.slice(index));
    if (call) {
      literal.push(`${call[1]}.${call[2]}`);
      return;
    }
    const table = TABLE_MAP_BODY.exec(appSource.slice(i === 0 ? 0 : sites[i - 1], index));
    if (table) tables.push(table[1]);
    else unaccounted.push(appSource.slice(index).split('\n')[0].trim());
  });
  return { literal, tables, unaccounted };
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

// The default import of services/websocket, by alias or relative path (a
// sibling `./websocket` included), with or without its extension; group 1 is
// the local name it is bound to.
const WS_IMPORT = /import\s+(\w+)\s+from\s+['"](?:@\/services\/|(?:\.\.?\/)+(?:services\/)?)websocket(?:\.js)?['"]/;
const LIFECYCLE_KEYS = new Set(['onReconnect', 'onVisibilityChange', 'showDisconnectedBanner']);

/**
 * Whether a module can subscribe to a WS event: it imports the service and
 * takes from it anything but the lifecycle keys. Only a destructuring that
 * names lifecycle keys alone is let through; binding the whole object or
 * chaining a call off it hands over on()/parsedOn() under any name.
 */
function subscribesToWs(source) {
  const imported = WS_IMPORT.exec(source);
  if (!imported) return false;
  return [...source.matchAll(new RegExp(`\\b${imported[1]}\\s*\\(`, 'g'))].some(({ index }) => {
    const destructured = /(?:const|let|var)\s*\{([^}]*)\}\s*=\s*$/.exec(source.slice(0, index));
    if (!destructured) return true;
    return destructured[1]
      .split(',')
      .map(entry => entry.split(':')[0].trim())
      .filter(Boolean)
      .some(key => !LIFECYCLE_KEYS.has(key));
  });
}

describe('WS event handlers live in App.vue, not in components', () => {
  const files = sourceFilesExceptWsLayer();

  it('scans a plausible number of source files', () => {
    expect(files.length).toBeGreaterThan(100);
  });

  it('the subscription pattern catches the shapes it exists for', () => {
    const IMPORT = "import useWebSocket from '@/services/websocket';\n";
    const subscribing = [
      `${IMPORT}const { on } = useWebSocket();`,
      "import useWebSocket from '@/services/websocket.js';\nconst { on } = useWebSocket();",
      'import useWebSocket from "@/services/websocket";\nconst { on } = useWebSocket();',
      "import useWebSocket from '../../services/websocket';\nconst { on } = useWebSocket();",
      "import useWebSocket from './websocket';\nlet { on } = useWebSocket();",
      `${IMPORT}const ws = useWebSocket();\nws.on('volume', 'volume_changed', h);`,
      `${IMPORT}const ws = useWebSocket();\nws.parsedOn('volume', 'volume_changed', schema, h);`,
      `${IMPORT}const { on: subscribe } = useWebSocket();`,
      `${IMPORT}useWebSocket().on('volume', 'volume_changed', h);`,
      "import useWs from '@/services/websocket';\nconst { on } = useWs();",
    ];
    for (const source of subscribing) expect(subscribesToWs(source), source).toBe(true);

    const lifecycleOnly = [
      `${IMPORT}const { onReconnect } = useWebSocket();`,
      `${IMPORT}const { onVisibilityChange, showDisconnectedBanner } = useWebSocket();`,
      "import { useFoo } from '@/composables/useFoo';\nemitter.on('change', h);",
    ];
    for (const source of lifecycleOnly) expect(subscribesToWs(source), source).toBe(false);
    // App.vue is the one module that must subscribe: the pattern reads it as doing so.
    expect(subscribesToWs(appSource)).toBe(true);
  });

  it('no module outside the WS layer subscribes to an event', () => {
    // useWebSocket also exposes onReconnect/onVisibilityChange — lifecycle
    // callbacks, not event handlers. A component may legitimately hook those
    // to refetch data only it displays. What it must not do is subscribe to an
    // event: that handler dies with the component, so events arriving while it
    // is unmounted are lost with nothing to heal them, and the state it fed
    // goes quietly stale.
    const offenders = files
      .filter(file => subscribesToWs(readFileSync(file, 'utf8')))
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
    for (const [, handler] of tableBlock(name).matchAll(/,\s*(\w+\.\w+)\]/g)) rows.push([name, handler]);
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
    //
    // Scope, measured: App.vue has no inline `parsedOn('c', 't',
    // wsEventRegistry['literal'], …)` left. Every subscription now comes from
    // PARSED_EVENTS, whose key is derived from the row, so a mismatch there is
    // unrepresentable and this list is empty by construction — not because the
    // live surface was checked and found sound. The rule is kept as a trap for
    // the inline form, which is still legal to write: planting one that names
    // the wrong schema does turn this red. Do not read it as cover for the table.
    expect(mismatched).toEqual([]);
  });
});

describe('App.vue subscriptions ↔ backend ws_events.py', () => {
  const backend = new Set(parseWsEventModels().keys());
  // PARSED_EVENTS is not read here: rule 2 ties it to wsEventRegistry, and
  // tests/schemas/ws.test.js ties every registry key to a backend model.
  const raw = tableRows('RAW_EVENTS').map(([c, t]) => `${c}.${t}`);
  const settings = settingsConfigTypes().map(type => `settings.${type}`);
  const calls = subscribeCalls();

  it('extracts a plausible surface from every side', () => {
    expect(raw.length).toBeGreaterThan(15);
    expect(settings.length).toBeGreaterThan(10);
    expect(calls.literal.length).toBeGreaterThan(10);
    expect(backend.size).toBeGreaterThan(50);
  });

  it('every on()/parsedOn() is a literal pair or a dispatch table read above', () => {
    // The pairs below come from the tables and the literal calls only. A
    // fourth table, or a call with a computed pair, would subscribe to events
    // this guardrail never reads — it has to be taught the new shape first.
    expect(calls.unaccounted).toEqual([]);
    expect([...calls.tables].sort()).toEqual(['PARSED_EVENTS', 'RAW_EVENTS', 'SETTINGS_CONFIG_EVENTS']);
  });

  it('every (category, type) App.vue subscribes to is declared in ws_events.py', () => {
    const undeclared = [...raw, ...settings, ...calls.literal]
      .filter(pair => !backend.has(pair));

    // The dispatcher matches on the exact pair: once a backend TYPE is renamed,
    // a handler on the old name receives nothing, with no error on either side.
    // Only this direction holds — an event with no subscriber here may be read
    // by Milo-Mac or Milo-iOS alone (routing/multiroom_error is pinned that way).
    expect(undeclared).toEqual([]);
  });
});
