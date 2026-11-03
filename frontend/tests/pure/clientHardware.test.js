// frontend/tests/pure/clientHardware.test.js
/**
 * ClientEdit reads a satellite's card list and current card whenever the
 * satellite is reachable and either is still unknown. If this fails, a panel
 * opened on a rebooting satellite never draws its "Audio card" section once
 * the satellite is back, and a failed read of the current card is never
 * retried, leaving Apply hidden since the dirty check needs the saved card.
 */
import { describe, it, expect } from 'vitest';
import { needsHardwareRead } from '@/utils/clientHardware';

describe('needsHardwareRead', () => {
  it('a reachable satellite whose cards or current card are unknown is read again', () => {
    expect(needsHardwareRead({ reachable: true, cardsLoaded: false, savedAudioId: '', inFlight: false })).toBe(true);
    // The card list arrived but GET /hardware failed.
    expect(needsHardwareRead({ reachable: true, cardsLoaded: true, savedAudioId: '', inFlight: false })).toBe(true);
    expect(needsHardwareRead({ reachable: true, cardsLoaded: false, savedAudioId: 'hifiberry-dacplus', inFlight: false })).toBe(true);
  });

  it('an unreachable satellite is never read', () => {
    for (const cardsLoaded of [false, true]) {
      for (const savedAudioId of ['', 'hifiberry-dacplus']) {
        expect(needsHardwareRead({ reachable: false, cardsLoaded, savedAudioId, inFlight: false })).toBe(false);
      }
    }
  });

  it('a satellite already known is not read on every return', () => {
    // The return from the reboot an Apply triggered: the reboot watcher has
    // already set the saved card to the one just applied.
    expect(needsHardwareRead({ reachable: true, cardsLoaded: true, savedAudioId: 'hifiberry-dacplus', inFlight: false })).toBe(false);
  });

  it('a read in flight is not started twice', () => {
    // An offline/online flap while the first read has not answered yet.
    expect(needsHardwareRead({ reachable: true, cardsLoaded: false, savedAudioId: '', inFlight: true })).toBe(false);
  });
});
