// frontend/tests/pure/multiroomZone.test.js
/**
 * A zone row whose members the server cannot count is published at a
 * fabricated DEFAULT_VOLUME_DB that no move changes; MultiroomItem draws a
 * slider only for 'counted'. If this fails, such a zone shows a live slider
 * that sets nothing and springs back, and a mute switch that never turns off.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { zoneLevelState, leavingDeletesZone, moveZoneTo } from '@/utils/multiroomZone';

describe('zoneLevelState', () => {
  it('a zone whose members are all unavailable has no level to draw', () => {
    expect(zoneLevelState([
      { online: false, volume_control: true },
      { online: false, volume_control: true }
    ])).toBe('offline');
    expect(zoneLevelState([])).toBe('offline');
    expect(zoneLevelState(null)).toBe('offline');
    // Offline outranks DAC: an all-DAC zone with nobody reachable is offline.
    expect(zoneLevelState([
      { online: false, volume_control: false },
      { online: false, volume_control: false }
    ])).toBe('offline');
  });

  it('a zone whose reachable members are all DACs has an external level', () => {
    expect(zoneLevelState([
      { online: true, volume_control: false },
      { online: false, volume_control: true }
    ])).toBe('external');
  });

  it('a reachable member with volume control makes the average real', () => {
    expect(zoneLevelState([
      { online: true, volume_control: true },
      { online: false, volume_control: true }
    ])).toBe('counted');
    // A client the registry has not described yet is not a DAC.
    expect(zoneLevelState([
      { online: true, volume_control: false },
      { online: true }
    ])).toBe('counted');
  });
});

/**
 * ClientEdit's delete confirmation names the zone a delete takes with it. If
 * this fails, deleting one of a pair's two speakers confirms "its place in its
 * zone" while the zone, its name and its crossover go too, the other speaker
 * left on its own — irreversibly, and unannounced.
 */
describe('leavingDeletesZone', () => {
  it('a zone of two is deleted with either member', () => {
    expect(leavingDeletesZone({ id: 'z1', client_ids: ['a', 'b'] })).toBe(true);
  });

  it('a larger zone keeps going without one', () => {
    expect(leavingDeletesZone({ id: 'z1', client_ids: ['a', 'b', 'c'] })).toBe(false);
  });

  it('a standalone speaker has no zone to take with it', () => {
    expect(leavingDeletesZone(null)).toBe(false);
    expect(leavingDeletesZone(undefined)).toBe(false);
  });
});

// A zone drag places each member's thumb where the backend's move will put it.
// The table holds this preview and the backend (test_zone_move_rule.py) to the
// same answers: when it fails here, a member's thumb follows the drag to one
// level and jumps to another when the server's echo lands.
const ZONE_MOVES = JSON.parse(readFileSync(
  resolve(dirname(fileURLToPath(import.meta.url)), '../../../backend/tests/contracts/zone_move_cases.json'),
  'utf8',
)).cases;

describe('moveZoneTo', () => {
  it('holds cases where a room stops at a limit', () => {
    expect(ZONE_MOVES.length).toBeGreaterThanOrEqual(6);
    expect(ZONE_MOVES.some(({ limits, moved }) => Object.values(moved)
      .some((db) => db === limits.min_db || db === limits.max_db))).toBe(true);
  });

  it.each(ZONE_MOVES.map((c) => [c.id, c]))('%s', (_id, { limits, levels, target_db: targetDb, moved }) => {
    const result = moveZoneTo(levels, targetDb, { min: limits.min_db, max: limits.max_db });
    expect(Object.keys(result).sort()).toEqual(Object.keys(moved).sort());
    for (const [macId, db] of Object.entries(moved)) expect(result[macId]).toBeCloseTo(db, 9);
  });
});
