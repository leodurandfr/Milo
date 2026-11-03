// frontend/tests/architecture/adoptionErrors.test.js
/**
 * Guardrail over the speaker adoption error codes, which the backend sends as
 * `detail.code` from `POST /api/discovery/adopt-speaker` and ConfigureSystem
 * renders here.
 *
 * An unknown code falls back to the generic "did not accept the configuration",
 * so a code the backend added and this side never learned tells the user
 * nothing about what to do — an already configured speaker that needs a reset
 * reads the same as an unreachable hotspot. The reverse is a key nobody can
 * reach.
 *
 * The backend side is read as text by `helpers/adoptionCodes.js`.
 */
import { describe, it, expect } from 'vitest';
import { ADOPTION_ERROR_KEYS, ADOPTION_ERROR_FALLBACK_KEY } from '@/utils/adoptionError';
import english from '@/locales/english.json';
import { backendAdoptionCodes } from '../helpers/adoptionCodes';

/** Resolve a dotted key against english.json, or undefined. */
function lookup(key) {
  return key.split('.').reduce((node, part) => node?.[part], english);
}

describe('speaker adoption error codes', () => {
  it('found the codes the backend sends', () => {
    // Guards the parse: an empty list would make both checks below vacuous.
    expect(backendAdoptionCodes().length).toBeGreaterThan(5);
  });

  it('maps every code the backend can send, and invents none', () => {
    expect(Object.keys(ADOPTION_ERROR_KEYS).sort()).toEqual(backendAdoptionCodes());
  });

  it('points every key at a string english.json actually carries', () => {
    // A key that resolves to undefined renders as the raw dotted path.
    const keys = [...Object.values(ADOPTION_ERROR_KEYS), ADOPTION_ERROR_FALLBACK_KEY];
    const missing = keys.filter(key => typeof lookup(key) !== 'string');

    expect(missing).toEqual([]);
  });
});
