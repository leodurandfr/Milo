// frontend/tests/pure/serverClock.test.js
/**
 * The server's clock, estimated from the `timestamp` every WebSocket envelope
 * carries. A position anchor is stamped on the server's clock and read on the
 * client's: a unit with no RTC battery that booted without internet runs hours
 * behind every phone, and read on the phone's own clock each anchor looked
 * hours old — the bar sat at the end of the track for the whole session. The
 * consumers are useSourceProgress and podcastStore.
 */
import { describe, it, expect } from 'vitest';
import { createServerClock } from '@/services/serverClock';

// Epoch milliseconds on the client's own clock.
const LOCAL = 1_750_000_000_000;

describe('createServerClock', () => {
  it('reads the client clock until an envelope arrives', () => {
    expect(createServerClock().now(LOCAL)).toBe(LOCAL);
  });

  it("a server clock 3 h behind still puts the playhead on the anchor's own time", () => {
    const clock = createServerClock();
    const behindMs = 3 * 3600 * 1000;
    // Envelopes stamped when sent, received after 5 to 180 ms on the wire.
    [180, 40, 5, 95, 60].forEach((latency, i) => {
      const sent = LOCAL + i * 30_000;
      clock.note((sent - behindMs) / 1000, sent + latency);
    });

    const local = LOCAL + 200_000;
    const server = clock.now(local);
    expect(server).toBeLessThanOrEqual(local - behindMs);
    expect(server).toBeGreaterThanOrEqual(local - behindMs - 5);
  });

  it('a forward step of the server clock is followed on the next envelope', () => {
    const clock = createServerClock();
    const behindMs = 3 * 3600 * 1000;
    for (let i = 0; i < 4; i++) clock.note((LOCAL + i * 1000 - behindMs) / 1000, LOCAL + i * 1000 + 20);

    // timesyncd steps the server three hours forward.
    clock.note((LOCAL + 5000) / 1000, LOCAL + 5000 + 20);

    expect(Math.abs(clock.now(LOCAL + 6000) - (LOCAL + 6000))).toBeLessThanOrEqual(20);
  });

  it('starts a new estimate after a reset', () => {
    const clock = createServerClock();
    clock.note((LOCAL - 60_000) / 1000, LOCAL);
    clock.reset();

    expect(clock.now(LOCAL)).toBe(LOCAL);
    clock.note((LOCAL + 60_000) / 1000, LOCAL);
    expect(clock.now(LOCAL)).toBe(LOCAL + 60_000);
  });

  it('ignores an envelope with no timestamp', () => {
    const clock = createServerClock();
    clock.note(undefined, LOCAL);

    expect(clock.now(LOCAL)).toBe(LOCAL);
  });
});
