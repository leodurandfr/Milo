// frontend/tests/pure/daylight.test.js
/**
 * utils/daylight.js decides when the kiosk's `auto` theme turns dark.
 *
 * The reference times are NOAA's solar-calculator algorithm for Paris (the
 * point the tz database gives Europe/Paris), in UTC, rounded to the minute.
 * A failure here is a kiosk going dark in daylight or staying white at night,
 * with nothing in any log to say why.
 */
import { describe, it, expect } from 'vitest';
import { isDaytime } from '@/utils/daylight';

const PARIS = { latitude: 48.8667, longitude: 2.3333 };
// Longyearbyen, Svalbard: midnight sun in June, polar night in December.
const SVALBARD = { latitude: 78.2167, longitude: 15.6333 };

const TOLERANCE_MS = 2 * 60 * 1000;

/** The theme turns within two minutes of NOAA's crossing, on the right side of it. */
function expectTurnAt(isoReference, { dayAfter }) {
  const reference = Date.parse(isoReference);
  const before = new Date(reference - TOLERANCE_MS);
  const after = new Date(reference + TOLERANCE_MS);
  expect(isDaytime(before, PARIS.latitude, PARIS.longitude)).toBe(!dayAfter);
  expect(isDaytime(after, PARIS.latitude, PARIS.longitude)).toBe(dayAfter);
}

function at(iso) {
  return new Date(iso);
}

describe('isDaytime', () => {
  it("turns at NOAA's sunrise and sunset for Paris at the June solstice", () => {
    expectTurnAt('2026-06-21T03:47:00Z', { dayAfter: true });
    expectTurnAt('2026-06-21T19:58:00Z', { dayAfter: false });
  });

  it("turns at NOAA's sunrise and sunset for Paris at the December solstice", () => {
    expectTurnAt('2026-12-21T07:42:00Z', { dayAfter: true });
    expectTurnAt('2026-12-21T15:56:00Z', { dayAfter: false });
  });

  it('is day at local midnight under the midnight sun, and night at noon in polar night', () => {
    expect(isDaytime(at('2026-06-21T23:00:00Z'), SVALBARD.latitude, SVALBARD.longitude)).toBe(true);
    expect(isDaytime(at('2026-12-21T11:00:00Z'), SVALBARD.latitude, SVALBARD.longitude)).toBe(false);
  });

  it('is always day without a location', () => {
    // A timezone with no city (Etc/UTC) answers null: the light theme stays.
    expect(isDaytime(at('2026-12-21T23:00:00Z'), null, null)).toBe(true);
    expect(isDaytime(at('2026-12-21T03:00:00Z'), null, 2.3333)).toBe(true);
  });
});
