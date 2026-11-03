// frontend/tests/pure/multiroomZone.test.js
/**
 * A zone row whose members the server cannot count is published at a
 * fabricated DEFAULT_VOLUME_DB that no move changes; MultiroomItem draws a
 * slider only for 'counted'. If this fails, such a zone shows a live slider
 * that sets nothing and springs back, and a mute switch that never turns off.
 */
import { describe, it, expect } from 'vitest';
import { zoneLevelState } from '@/utils/multiroomZone';

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
