// frontend/tests/architecture/settingsBulkContract.test.js
/**
 * Structural guardrail over the one payload that carries every settings
 * category: `GET /api/settings/bulk`.
 *
 * Why it exists: `settingsStore.doLoadAllSettings()` used to restate the whole
 * backend default set on the frontend — `?? -80.0`, `?? 2.0`, `?? 120`, sixteen
 * categories of them — against CLAUDE.md's "backend-derived values are fetched
 * at runtime, not hardcoded on both sides". None of those fallbacks could ever
 * fire: `BulkSettingsResponse` declares every category as a REQUIRED field, so
 * a 200 either carries them all or FastAPI raises and the store sees no data at
 * all. They were a second declaration that could only ever disagree with the
 * first, silently, in the direction of showing a stale default as if it were the
 * stored value.
 *
 * The rules are derived from the backend models — `api/responses.py` and
 * `core/models/settings_config.py` — never from a fixture, so a category added,
 * renamed or dropped on the backend surfaces here rather than at runtime.
 *
 * Known limit, stated rather than papered over: rule 4 assumes every bulk field
 * has a frontend consumer. That holds today (Milo-Mac reads `volume_limits` and
 * `dock_apps`, both of which the store reads too). A field served *only* for
 * Milo-Mac would go red here and belongs in an allowlist with its reason, the
 * same way `tests/i18n/` allowlists the translations that legitimately diverge.
 *
 * Every extraction asserts it found a plausible surface first — a broken parse
 * must fail loudly, not pass on an empty set.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const BACKEND = resolve(HERE, '../../../backend');
const STORE_PATH = resolve(HERE, '../../src/stores/settingsStore.js');

const RESPONSES = readFileSync(resolve(BACKEND, 'api/responses.py'), 'utf8');
const CONFIGS = readFileSync(resolve(BACKEND, 'core/models/settings_config.py'), 'utf8');
const STORE = readFileSync(STORE_PATH, 'utf8');

/** `BulkSettingsResponse`'s fields: the wire key → the config model typing it. */
function bulkFields() {
  const m = RESPONSES.match(/class BulkSettingsResponse\(BaseModel\):\n([\s\S]*?)\n\n\n/);
  if (!m) throw new Error('BulkSettingsResponse not found in api/responses.py — the extractor is broken');
  const fields = new Map(
    [...m[1].matchAll(/^ {4}([a-z_]+): (\w+)$/gm)].map(x => [x[1], x[2]])
  );
  if (fields.size < 15) {
    throw new Error(`parsed ${fields.size} bulk fields — the extractor is broken`);
  }
  return fields;
}

/** Each `*Config` model in settings_config.py → the field names it declares. */
function configModels() {
  const models = new Map();
  for (const block of CONFIGS.split(/\nclass /).slice(1)) {
    const name = block.match(/^(\w+)\(BaseModel\):/)?.[1];
    if (!name) continue;
    const body = block.slice(block.indexOf('\n'));
    models.set(name, [...body.matchAll(/^ {4}([a-z_]+): /gm)].map(x => x[1]));
  }
  if (models.size < 10) {
    throw new Error(`parsed ${models.size} config models — the extractor is broken`);
  }
  return models;
}

/**
 * The body of `doLoadAllSettings`, comments stripped: a rule must read code,
 * never the prose above it (the App.vue lesson).
 */
function loaderBody() {
  const m = STORE.match(/async function doLoadAllSettings\(\) \{([\s\S]*?)\n {2}\}/);
  if (!m) throw new Error('doLoadAllSettings not found in settingsStore.js — the extractor is broken');
  const body = m[1].replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
  if (!body.includes('/api/settings/bulk')) {
    throw new Error('doLoadAllSettings no longer reads /api/settings/bulk — the extractor is broken');
  }
  return body;
}

/** Every `d.<key>` the loader reads out of the bulk payload. */
function keysRead(body) {
  const keys = new Set([...body.matchAll(/\bd\.([a-z_]+)/g)].map(m => m[1]));
  if (keys.size < 10) {
    throw new Error(`parsed ${keys.size} payload reads — the extractor is broken`);
  }
  return keys;
}

/** Categories assigned wholesale: `setIfChanged(<ref>, d.<key>)`. */
function wholesaleAssignments(body) {
  return [...body.matchAll(/setIfChanged\((\w+), d\.([a-z_]+)\)/g)].map(m => ({ ref: m[1], key: m[2] }));
}

/** A store ref's initial shape — the placeholder bound before the first load. */
function refInitialKeys(name) {
  const m = STORE.match(new RegExp(`const ${name} = ref\\(\\{([\\s\\S]*?)\\n {2}\\}\\)`));
  if (!m) throw new Error(`the initialiser of ${name} was not found — the extractor is broken`);
  return [...m[1].matchAll(/^\s*([a-z_]+):/gm)].map(x => x[1]);
}

const BULK = bulkFields();
const MODELS = configModels();
const BODY = loaderBody();
const READ = keysRead(BODY);
const WHOLESALE = wholesaleAssignments(BODY);

describe('settings/bulk ↔ settingsStore', () => {
  it('parsed a plausible surface on both sides', () => {
    // Guards every rule below: an empty parse would make them vacuous.
    expect([...BULK.keys()]).toContain('volume_limits');
    expect(MODELS.get('VolumeLimitsConfig')).toEqual(['min_db', 'max_db']);
    expect(READ.has('mac_roc')).toBe(true);
    expect(WHOLESALE.length).toBeGreaterThanOrEqual(8);
  });

  it('reads no key the bulk response does not declare', () => {
    // A typo binds `undefined` into a ref with no error at boot — the panel
    // shows the placeholder default and the stored value never arrives.
    const unknown = [...READ].filter(k => !BULK.has(k));
    expect(unknown).toEqual([]);
  });

  it('restates no default the backend already owns', () => {
    // Every bulk field is required, so a fallback here cannot fire — it can only
    // drift from the backend value it duplicates.
    const fallbacks = BODY.split('\n')
      .filter(line => /\bd\.[a-z_]/.test(line) && /(\?\?|\|\||\?\.)/.test(line))
      .map(line => line.trim());
    expect(fallbacks).toEqual([]);
  });

  it('leaves no bulk field unread', () => {
    // A category the backend serves and nobody reads is the write-only payload
    // this programme keeps finding; see the docstring for the Milo-Mac caveat.
    const unread = [...BULK.keys()].filter(k => k !== 'status' && !READ.has(k));
    expect(unread).toEqual([]);
  });

  it('gives each wholesale-assigned ref the shape its backend model declares', () => {
    // `setIfChanged(ref, d.category)` adopts the backend object as-is, so the
    // ref's initialiser is the pre-load placeholder for exactly those keys. A
    // backend field the placeholder lacks binds `undefined` until the first
    // load lands — the class of bug phase 1c found on the snapclient buffer.
    const mismatched = WHOLESALE
      .map(({ ref, key }) => ({
        ref,
        expected: MODELS.get(BULK.get(key)) ?? [`no model for ${key}`],
        actual: refInitialKeys(ref),
      }))
      .filter(({ expected, actual }) => [...expected].sort().join() !== [...actual].sort().join());
    expect(mismatched).toEqual([]);
  });
});
