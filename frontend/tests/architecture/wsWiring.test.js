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

/** The registry keys App.vue actually passes to parsedOn(). */
function subscribedRegistryKeys() {
  return [...appSource.matchAll(/wsEventRegistry\['([^']+)'\]/g)].map(m => m[1]);
}

/** The (category, type) pairs App.vue passes to parsedOn(), as 'category.type'. */
function parsedOnPairs() {
  return [...appSource.matchAll(/parsedOn\(\s*'([^']+)'\s*,\s*'([^']+)'/g)]
    .map(m => `${m[1]}.${m[2]}`);
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

describe('wsEventRegistry ↔ App.vue parsedOn() subscriptions', () => {
  const declared = registryKeys();
  const subscribed = subscribedRegistryKeys();
  const pairs = parsedOnPairs();

  it('extracts a plausible surface from both sides', () => {
    expect(declared.length).toBeGreaterThan(5);
    expect(subscribed.length).toBeGreaterThan(5);
    expect(pairs.length).toBe(subscribed.length);
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
    const mismatched = pairs
      .map((pair, i) => ({ pair, key: subscribed[i] }))
      .filter(({ pair, key }) => pair !== key);

    // Subscribing to (a, b) while validating against the schema for (c, d)
    // logs a bogus validation warning and falls back to the raw payload.
    expect(mismatched).toEqual([]);
  });
});
