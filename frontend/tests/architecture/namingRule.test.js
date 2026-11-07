// frontend/tests/architecture/namingRule.test.js
/**
 * Milō's screen draws a track exactly when the Lock Screen names one.
 *
 * The screen decides it in `richSourceFor`, the push in the backend's
 * `payloads.shown_track`, and Milo-iOS / Milo-Mac in `MiloAudioState.shown`.
 * One table, `backend/tests/contracts/connected_naming_cases.json`, holds the
 * screen and the push to the same answers (the backend's test_naming_rule.py
 * reads it for the push); the apps' rule is pinned word for word by the
 * backend's test_milo_ios_contract.py, since no Swift runs here. When it fails
 * here, the screen and the Lock Screen disagree about a sender Milō cannot call
 * playing or paused: the kiosk says "Connected to X" while the phone names a
 * track, or the reverse.
 *
 * The table's `_domain` says what it leaves out — the 300 px AirPlay gate, the
 * browser sources, an availability reason — and each case is checked to sit
 * inside it, since there the screen differs from the push by design.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { richSourceFor } from '../../src/composables/useRichDisplay.js';
import { BROWSER_SOURCES } from '../../src/constants/audioSources.js';
import { UNTRUSTED_SENDER_MIN_ARTWORK_PX } from '../../src/constants/imageQuality.js';

const HERE = dirname(fileURLToPath(import.meta.url));
const TABLE = JSON.parse(readFileSync(
  resolve(HERE, '../../../backend/tests/contracts/connected_naming_cases.json'), 'utf8',
));
const CASES = TABLE.cases;

describe('connected_naming_cases.json', () => {
  it('holds cases of both answers', () => {
    expect(CASES.length).toBeGreaterThanOrEqual(8);
    expect(new Set(CASES.map((c) => c.shown))).toEqual(new Set([true, false]));
  });

  it.each(CASES.map((c) => [c.id, c]))('%s', (_id, { state, shown }) => {
    expect(BROWSER_SOURCES).not.toContain(state.source);
    if (state.source === 'airplay' && state.session?.artwork) {
      expect(state.details.artwork_width).toBeGreaterThan(UNTRUSTED_SENDER_MIN_ARTWORK_PX);
    }

    expect(richSourceFor(state) !== null).toBe(shown);
  });
});
