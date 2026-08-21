// frontend/tests/architecture/eqFrequencyContract.test.js
/**
 * The ten EQ band frequencies are declared on both sides. They must agree.
 *
 * `equalizerStore.js` builds its band list from `DEFAULT_FREQUENCIES` whenever
 * the backend has no stored filters yet; `presets.py` builds the real filter
 * chain from `DEFAULT_EQ_FREQS`. Nothing tied the two: a band added or a
 * frequency corrected on the backend leaves the EQ page drawing sliders
 * labelled with the old frequencies while CamillaDSP applies the new ones —
 * silent, and visible only as "the 4 kHz slider does not sound like 4 kHz".
 *
 * This replaces a backend test that claimed to check exactly this and opened
 * no frontend file at all: it retyped the frontend's list into its own body,
 * which made it a third declaration of the same ten numbers and left it green
 * through every change the store could make.
 *
 * The mould is the repo's own — `tests/schemas/api.test.js` reads the
 * AudioSource enum out of `backend/core/models/audio_state.py`, and
 * `settingsBulkContract.test.js` reads `backend/api/responses.py`. Both sides
 * are read as source, neither is restated here, and each extractor asserts it
 * found a plausible surface before comparing.
 *
 * Known scope, stated rather than papered over: CLAUDE.md § Frontend says a
 * backend-derived value should be fetched at runtime, not hardcoded on both
 * sides. That is a separate change; while `DEFAULT_FREQUENCIES` exists, this is
 * what keeps it honest. If it ever becomes a runtime fetch, delete this file
 * with it.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const PRESETS_PATH = resolve(HERE, '../../../backend/core/equalizer/presets.py');
const STORE_PATH = resolve(HERE, '../../src/stores/equalizerStore.js');

/** The numbers on the right-hand side of `NAME = [...]`, from source text. */
function numberList(source, declaration) {
  const match = new RegExp(`${declaration}\\s*=\\s*\\[([^\\]]*)\\]`).exec(source);
  if (match === null) return null;
  return match[1]
    .split(',')
    .map(part => part.trim())
    .filter(part => part.length > 0)
    .map(Number);
}

describe('EQ band frequencies ↔ backend DEFAULT_EQ_FREQS', () => {
  const backend = numberList(readFileSync(PRESETS_PATH, 'utf8'), 'DEFAULT_EQ_FREQS');
  const frontend = numberList(readFileSync(STORE_PATH, 'utf8'), 'const DEFAULT_FREQUENCIES');

  it('reads a plausible list from each side', () => {
    // Without this, a renamed constant on either side would empty the
    // comparison and every assertion below would pass on nothing.
    expect(backend, 'DEFAULT_EQ_FREQS not found in presets.py — the extractor is broken').not.toBeNull();
    expect(frontend, 'DEFAULT_FREQUENCIES not found in equalizerStore.js — the extractor is broken').not.toBeNull();
    expect(backend.length).toBeGreaterThanOrEqual(10);
    expect(backend.every(Number.isFinite)).toBe(true);
    expect(frontend.every(Number.isFinite)).toBe(true);
  });

  it('declares the same frequencies, in the same order', () => {
    // Order matters as much as membership: the store maps index to band, so a
    // reordering relabels every slider without changing the set.
    expect(frontend).toEqual(backend);
  });
});
