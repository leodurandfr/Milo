// frontend/tests/helpers/adoptionCodes.js
/**
 * The error codes `POST /api/discovery/adopt-speaker` answers with, read as
 * text from `backend/api/discovery.py` — nothing of the backend is bundled.
 *
 * Its `_ADOPTION_CLIENT_ERROR_CODES` table is enough for every code an
 * `AdoptionError` carries: `backend/tests/test_api_discovery.py` already
 * proves each code `wifi_adoption.py` raises sits in it. `internal_error` is
 * the route's own answer to anything else.
 */
import { readFileSync } from 'fs';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';

const HERE = dirname(fileURLToPath(import.meta.url));
const DISCOVERY_PATH = resolve(HERE, '../../../backend/api/discovery.py');

export function backendAdoptionCodes() {
  const source = readFileSync(DISCOVERY_PATH, 'utf8');
  const table = source.match(/^_ADOPTION_CLIENT_ERROR_CODES = \{([\s\S]*?)^\}/m);
  const codes = table ? [...table[1].matchAll(/^\s+"([a-z_]+)":/gm)].map(m => m[1]) : [];
  if (/["']internal_error["']/.test(source)) codes.push('internal_error');
  return codes.sort();
}
