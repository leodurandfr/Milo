// frontend/tests/architecture/keepalive.test.js
/**
 * The frontend's keepalive deadline must outlast the backend's ping interval.
 *
 * `services/websocket.js` refreshes `lastPingTime` only on a `system/ping`, and
 * closes the socket once that age passes `pingStaleMs` — on a periodic check
 * and when the tab comes back. The pings come from `_send_ping` in
 * `backend/ws/manager.py`, every `PING_INTERVAL` seconds. The deadline was
 * written by hand from the interval, and nothing tied the two: an interval
 * raised past the deadline makes every visible client close a healthy socket,
 * reconnect and run App.vue's full `resyncStores('reconnect')` every couple of
 * minutes, forever, with no test going red.
 *
 * The bound is two intervals: a ping held up by as much as a whole interval (an
 * event-loop stall, a slow send) must not cost a reconnect. Both sides are read
 * as source, neither value is restated here, and each extractor asserts it
 * found exactly one declaration before comparing; the last test checks that
 * the two declarations are the ones the code actually uses.
 *
 * Known scope: CLAUDE.md § Frontend says a backend-derived value should reach
 * the frontend at runtime rather than be hardcoded on both sides. That is a
 * separate change; while `pingStaleMs` is a literal, this keeps it honest. If
 * it ever becomes a runtime value, delete this file with it.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { stripComments } from '../helpers/stripComments.js';

const HERE = dirname(fileURLToPath(import.meta.url));
const MANAGER_PATH = resolve(HERE, '../../../backend/ws/manager.py');
const WEBSOCKET_PATH = resolve(HERE, '../../src/services/websocket.js');

/** Every capture of `pattern` in `source`, as numbers. */
function numbers(source, pattern) {
  return [...source.matchAll(pattern)].map(match => Number(match[1].replaceAll('_', '')));
}

describe('WebSocket keepalive ↔ backend PING_INTERVAL', () => {
  // Anchored at the start of a line, so the module docstring's prose about the
  // ping heartbeat is never read as the declaration.
  const intervals = numbers(
    readFileSync(MANAGER_PATH, 'utf8'),
    /^PING_INTERVAL(?:\s*:\s*\w+)?\s*=\s*(\d[\d_]*(?:\.\d+)?)\s*(?:#.*)?$/gm,
  );
  // Comments stripped, so the prose beside the literal is never read as it.
  const deadlines = numbers(
    stripComments(readFileSync(WEBSOCKET_PATH, 'utf8')),
    /this\.pingStaleMs\s*=\s*(\d[\d_]*)\s*;/g,
  );

  it('reads exactly one positive value from each side', () => {
    // Without this, a renamed or reshaped declaration would leave nothing to
    // compare and the bound below would never be checked.
    expect(intervals, 'PING_INTERVAL in backend/ws/manager.py — the extractor is broken').toHaveLength(1);
    expect(deadlines, 'this.pingStaleMs in services/websocket.js — the extractor is broken').toHaveLength(1);
    expect(intervals[0]).toBeGreaterThan(0);
    expect(deadlines[0]).toBeGreaterThan(0);
  });

  it('the stale deadline spans at least two backend ping intervals', () => {
    const [intervalS] = intervals;
    const [deadlineMs] = deadlines;
    expect(
      deadlineMs,
      `pingStaleMs (${deadlineMs} ms) must be at least two PING_INTERVALs (${intervalS} s)`,
    ).toBeGreaterThanOrEqual(2 * intervalS * 1000);
  });

  it('the values read are the ones the keepalive uses', () => {
    // A literal slipped into the sleep or the staleness checks would leave both
    // declarations, and the bound above, describing a keepalive nothing runs.
    const manager = readFileSync(MANAGER_PATH, 'utf8');
    const websocket = stripComments(readFileSync(WEBSOCKET_PATH, 'utf8'));
    expect(manager).toMatch(/asyncio\.sleep\(PING_INTERVAL\)/);
    expect([...websocket.matchAll(/>\s*this\.pingStaleMs\b/g)]).toHaveLength(2);
  });
});
