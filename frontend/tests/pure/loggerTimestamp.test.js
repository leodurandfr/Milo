// frontend/tests/pure/loggerTimestamp.test.js
/**
 * The log timestamp is the same string everywhere.
 *
 * It used to be `toLocaleTimeString('fr-FR', …)`, which is a locale chosen for
 * a line nobody reads in French — and any locale here means the separator and
 * the digit shape change with whoever is looking. A frontend log is read next
 * to a journal line, so it takes one shape: HH:MM:SS.mmm, 24-hour, dot before
 * the milliseconds.
 *
 * Asserted against a fixed *local* instant, not an epoch, so the expectation
 * does not move with the runner's timezone. `fr-FR` would produce a comma here
 * and go red.
 */
import { describe, it, expect, afterEach, vi } from 'vitest';

import { logger } from '@/services/logger';

afterEach(() => {
  vi.useRealTimers();
});

describe('logger timestamp', () => {
  it('formats a local instant as HH:MM:SS.mmm', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 8, 4, 14, 5, 9, 123));

    expect(logger.getTimestamp()).toBe('14:05:09.123');
  });

  it('pads every field, so the column never shifts', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 0, 1, 7, 3, 4, 7));

    expect(logger.getTimestamp()).toBe('07:03:04.007');
  });
});
