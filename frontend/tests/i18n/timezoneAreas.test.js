// frontend/tests/i18n/timezoneAreas.test.js
/**
 * The timezone Area dropdown labels each tz database area the backend offers
 * (`PLACE_AREAS` in `api/system.py`) from `timezone.areas.<Area>`. An area
 * offered with no key renders as the raw key path; a key with no area is dead.
 * Both lists are spelled once each, on either side of the wire, so they are
 * held equal here. The other locales are held to english.json's keys by
 * `locales.test.js`.
 *
 * Reads the backend tuple as text, the way `languageList.test.js` does.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'fs';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';

const HERE = dirname(fileURLToPath(import.meta.url));
const BACKEND_PATH = resolve(HERE, '../../../backend/api/system.py');
const ENGLISH_PATH = resolve(HERE, '../../src/locales/english.json');

function backendAreas() {
  const tuple = readFileSync(BACKEND_PATH, 'utf8').match(/PLACE_AREAS\s*=\s*\(([^)]+)\)/);
  return tuple ? [...tuple[1].matchAll(/"([A-Za-z_]+)"/g)].map(m => m[1]).sort() : [];
}

describe('timezone areas', () => {
  it('labels exactly the areas the backend offers', () => {
    const offered = backendAreas();
    const labelled = Object.keys(JSON.parse(readFileSync(ENGLISH_PATH, 'utf8')).timezone.areas).sort();

    // A parse that found nothing must fail here, not pass on two empty lists.
    expect(offered.length).toBeGreaterThan(5);
    expect(labelled).toEqual(offered);
  });
});
