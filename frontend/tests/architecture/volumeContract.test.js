// frontend/tests/architecture/volumeContract.test.js
/**
 * Two volume numbers the screen needs before, or beside, what the server
 * sends, each declared on both sides and held equal here — read from source,
 * as `eqFrequencyContract.test.js` reads the EQ bands.
 *
 * - `DEFAULT_VOLUME_DB`, the level a client is drawn at before the server has
 *   sent one. Four literals stood in for it (-45 in the schema and the store's
 *   first frame, -30 for an unknown client, -60 in the zone rows): a slider
 *   could open on one number and jump to another with the first broadcast.
 * - `MAX_ADJUST_DB`, the largest delta `/api/volume/adjust` takes. The dock
 *   sends a held button's presses as sums, in parts of at most this: narrowed
 *   on the backend alone, every part would be refused with a 422 and the
 *   gesture lost.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { DEFAULT_VOLUME_DB, MAX_ADJUST_DB } from '@/constants/volume';

const HERE = dirname(fileURLToPath(import.meta.url));
const BACKEND = resolve(HERE, '../../../backend');

describe('volume constants ↔ backend', () => {
  it('DEFAULT_VOLUME_DB is the backend\'s default level', () => {
    const source = readFileSync(resolve(BACKEND, 'config/constants.py'), 'utf8');
    const match = /^DEFAULT_VOLUME_DB\s*=\s*(-?[\d.]+)/m.exec(source);

    expect(match, 'DEFAULT_VOLUME_DB not found in constants.py — the extractor is broken').not.toBeNull();
    expect(DEFAULT_VOLUME_DB).toBe(Number(match[1]));
  });

  it('MAX_ADJUST_DB is the bound /api/volume/adjust puts on a delta, both ways', () => {
    const source = readFileSync(resolve(BACKEND, 'api/models.py'), 'utf8');
    const match = /class VolumeAdjustRequest[\s\S]*?delta_db:[^\n]*ge=(-?[\d.]+),\s*le=(-?[\d.]+)/.exec(source);

    expect(match, 'VolumeAdjustRequest.delta_db bounds not found in models.py — the extractor is broken').not.toBeNull();
    expect([-MAX_ADJUST_DB, MAX_ADJUST_DB]).toEqual([Number(match[1]), Number(match[2])]);
  });
});
