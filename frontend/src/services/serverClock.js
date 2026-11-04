// frontend/src/services/serverClock.js
// The server's clock, as this client estimates it. A position anchor is
// stamped on the server's clock (`at`) and read against "now": read on the
// client's own clock, a unit with no RTC battery that booted without internet
// — hours behind every phone — put the bar at the end of the track for the
// whole session.
//
// Every WebSocket envelope carries the server's `timestamp` at sending. Its
// gap to the client's clock at reception is the offset plus the time on the
// wire, which only widens it: the least gap of the last few envelopes is the
// estimate. A server stepped forward (timesyncd, once the internet answers)
// narrows the gap and is followed on the next envelope; one stepped back only
// once the narrower gaps have left the window.

const WINDOW = 8;

export function createServerClock() {
  let gaps = [];

  return {
    /** An envelope stamped `serverTs` (epoch seconds) received at `localNowMs`. */
    note(serverTs, localNowMs) {
      if (!Number.isFinite(serverTs)) return;
      gaps.push(localNowMs - serverTs * 1000);
      if (gaps.length > WINDOW) gaps.shift();
    },

    /** The server's epoch milliseconds at `localNowMs` (the client's clock without an envelope yet). */
    now(localNowMs) {
      return gaps.length ? localNowMs - Math.min(...gaps) : localNowMs;
    },

    reset() {
      gaps = [];
    },
  };
}

export const serverClock = createServerClock();

/** The server's clock now, in epoch milliseconds: what a position anchor is read against. */
export function serverNowMs() {
  return serverClock.now(Date.now());
}
