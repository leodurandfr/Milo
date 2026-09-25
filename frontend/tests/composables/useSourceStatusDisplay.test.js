// frontend/tests/composables/useSourceStatusDisplay.test.js
/**
 * The status card's sentence, from the audio state: what the card says a
 * source is doing, and what stops it from working.
 *
 * The phase is the card's word for a session — a paused session saying
 * "Playing" (E26) is what reading a single `is_playing` did — and the CD's two
 * operations under way (reading a disc, ejecting) outrank the session because
 * the drive is busy either way.
 */
import { describe, it, expect } from 'vitest';

import {
  DISPLAY_STATES,
  displayStateFor,
  statusCardKey,
  unavailableReasonFor,
} from '@/composables/useSourceStatusDisplay';
import { makeAudioState, makeSession } from '../helpers/audioState';

function stateOf(overrides) {
  return displayStateFor(makeAudioState({ source: 'cd', service: 'running', ...overrides }));
}

describe('displayStateFor', () => {
  it('names each session phase as it is, a pause included (E26)', () => {
    for (const phase of ['loading', 'playing', 'paused', 'connected']) {
      expect(stateOf({ session: makeSession({ phase }) })).toBe(phase);
    }
  });

  it('is ready with a running service and no session', () => {
    expect(stateOf({})).toBe('ready');
  });

  it('is starting while the service starts or a switch is in flight', () => {
    expect(stateOf({ service: 'starting' })).toBe('starting');
    expect(stateOf({ switching: true, session: makeSession() })).toBe('starting');
  });

  it('is an error for a failed service, whatever the session says', () => {
    expect(stateOf({
      service: 'failed',
      service_error: { reason: 'start_timeout', message: 'timed out' },
    })).toBe('error');
  });

  it("draws the drive's operations under way over the session", () => {
    expect(stateOf({ availability: { cd: 'reading_disc' } })).toBe('loading_disc');
    expect(stateOf({ availability: { cd: 'ejecting' }, session: makeSession() })).toBe('ejecting');
  });

  it('only ever answers a state the card can render', () => {
    const answers = [
      stateOf({}),
      stateOf({ service: 'starting' }),
      stateOf({ service: 'failed', service_error: { reason: 'start_failed', message: 'x' } }),
      stateOf({ availability: { cd: 'reading_disc' } }),
      stateOf({ availability: { cd: 'ejecting' } }),
      stateOf({ session: makeSession({ phase: 'paused' }) }),
    ];
    for (const answer of answers) expect(DISPLAY_STATES).toContain(answer);
  });
});

describe('unavailableReasonFor', () => {
  it("reads the selected source's own entry, not another's", () => {
    const availability = makeAudioState({ availability: { radio: 'no_internet' } }).availability;

    expect(unavailableReasonFor('radio', availability)).toBe('no_internet');
    expect(unavailableReasonFor('bluetooth', availability)).toBeNull();
  });

  it('names a missing disc, not an operation under way', () => {
    expect(unavailableReasonFor('cd', { cd: 'no_disc' })).toBe('no_disc');
    expect(unavailableReasonFor('cd', { cd: 'reading_disc' })).toBeNull();
  });

  it("leaves the Music Library's storage states to its own view (D13)", () => {
    expect(unavailableReasonFor('music_library', { music_library: 'no_storage' })).toBeNull();
    expect(unavailableReasonFor('music_library', { music_library: 'catalog_unavailable' })).toBeNull();
  });
});

describe('statusCardKey', () => {
  it('keeps one card while a named sender moves through its phases', () => {
    // Measured on AirPlay (iPhone and Mac): a sender starting to play goes
    // connected, loading, then playing before its cover arrives. Every step
    // reads "Connecté à <sender>", and a key per phase replayed the card's
    // entrance at each one.
    const keys = ['connected', 'loading', 'playing', 'paused'].map(
      (phase) => statusCardKey('airplay', phase, null, ['iPhone de Léo']),
    );
    expect(new Set(keys).size).toBe(1);
  });

  it('crosses over when the sender is named', () => {
    expect(statusCardKey('airplay', 'connected', null, []))
      .not.toBe(statusCardKey('airplay', 'connected', null, ['iPhone de Léo']));
  });

  it('crosses over between phases the card words differently', () => {
    // No sender to name (Spotify, Tidal, Qobuz): the phase is the card's line.
    expect(statusCardKey('spotify', 'loading', null, []))
      .not.toBe(statusCardKey('spotify', 'playing', null, []));
  });

  it('crosses over when a prerequisite goes missing under the same state', () => {
    expect(statusCardKey('cd', 'ready', null, []))
      .not.toBe(statusCardKey('cd', 'ready', 'no_drive', []));
  });
});
