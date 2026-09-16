// frontend/tests/architecture/sourceErrors.test.js
/**
 * Guardrail over the `source/error` reason codes, which are declared on the
 * backend and rendered here.
 *
 * The drift is silent by construction: an unknown code falls back to the
 * generic string, so a code the backend added and this side never learned
 * shows "Something went wrong" on a failure that had a precise wording
 * waiting for it — on every unit, in all eight languages, with nothing in any
 * log. The reverse is dead weight: a key nobody can ever reach.
 *
 * Reads `ws_events.py` as text, the way `schemas/api.test.js` reads
 * `audio_state.py` — nothing of the backend is bundled.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'fs';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';
import { SOURCE_ERROR_KEYS, SOURCE_ERROR_FALLBACK_KEY } from '@/constants/sourceErrors';
import english from '@/locales/english.json';

const HERE = dirname(fileURLToPath(import.meta.url));
const WS_EVENTS_PATH = resolve(HERE, '../../../backend/core/models/ws_events.py');

/** The codes `SourceErrorReason` declares, as the backend spells them. */
function backendReasons() {
  const body = readFileSync(WS_EVENTS_PATH, 'utf8')
    .match(/class SourceErrorReason:[\s\S]*?\n\n\nclass /);
  return body ? [...body[0].matchAll(/^\s{4}[A-Z_]+ = "([a-z_]+)"/gm)].map(m => m[1]).sort() : [];
}

/** Resolve a dotted key against english.json, or undefined. */
function lookup(key) {
  return key.split('.').reduce((node, part) => node?.[part], english);
}

describe('source error reasons', () => {
  it('found the codes the backend declares', () => {
    // Guards the parse: an empty list would make both checks below vacuous.
    expect(backendReasons().length).toBeGreaterThan(3);
  });

  it('maps every code the backend can send, and invents none', () => {
    expect(Object.keys(SOURCE_ERROR_KEYS).sort()).toEqual(backendReasons());
  });

  it('points every key at a string english.json actually carries', () => {
    // A key that resolves to undefined renders as the raw dotted path.
    const keys = [...Object.values(SOURCE_ERROR_KEYS), SOURCE_ERROR_FALLBACK_KEY];
    const missing = keys.filter(key => typeof lookup(key) !== 'string');

    expect(missing).toEqual([]);
  });
});
