// frontend/tests/pure/logCorrection.test.js
/**
 * The text "Copy for correction" hands whoever fixes a fault. If it breaks,
 * the correction is aimed at the wrong place: the event's own line is not the
 * one marked among its neighbors (or two are), a source read *now* is passed
 * off as the one playing *then*, a traceback's lines lose the line they belong
 * to, or the release and the places the backend could not read go missing.
 */
import { describe, it, expect } from 'vitest';
import { correctionText, EVENT_MARK } from '@/utils/logCorrection';

const EVENT = {
  template: 'stream stalled after # ms',
  level: 'warning',
  service: 'milo-backend.service',
  logger: 'backend.shared.mpv',
  count: 3,
  first_at: '2026-11-27T09:00:00.000001Z',
  last_at: '2026-11-27T10:00:00.123456Z',
  sample: 'stream stalled after 812 ms',
};

function line(i, overrides = {}) {
  return {
    at: `2026-11-27T10:00:${String(i).padStart(2, '0')}.000000Z`,
    level: 'info',
    service: 'milo-snapserver-multiroom.service',
    logger: null,
    message: `[Info] tick ${i}`,
    ...overrides,
  };
}

function context(overrides = {}) {
  const lines = Array.from({ length: 41 }, (_, i) => line(i));
  lines[20] = line(20, {
    level: 'warning', service: EVENT.service, logger: EVENT.logger,
    message: 'stream stalled after 812 ms\nTraceback (most recent call last):\n  File "mpv.py", line 3',
  });
  return {
    at: EVENT.last_at,
    event_index: 20,
    lines,
    build: { version: 'v0.3.0-12-g1c45d1d2', commit: '1c45d1d2aaaa' },
    source_state: {
      basis: 'logged_transition', source: 'radio',
      at: '2026-11-27T09:58:00Z', detail: 'the last transition logged before this moment',
    },
    not_collected: [],
    ...overrides,
  };
}

const marked = (text) => text.split('\n').filter(l => l.startsWith(EVENT_MARK));

describe('correctionText', () => {
  it('marks the event, and only it, with its neighbors on both sides', () => {
    const text = correctionText(EVENT, context());
    const rows = text.split('\n');
    const lines = rows.slice(rows.findIndex(r => r.startsWith('around it')) + 1);

    expect(marked(text)).toHaveLength(1);
    expect(marked(text)[0]).toContain('stream stalled after 812 ms');
    const markAt = lines.findIndex(r => r.startsWith(EVENT_MARK));
    expect(lines.slice(0, markAt).filter(r => r.includes('[Info] tick'))).toHaveLength(20);
    expect(lines.slice(markAt).filter(r => r.includes('[Info] tick'))).toHaveLength(20);
  });

  it('keeps a traceback under its own line, never at the margin', () => {
    const rows = correctionText(EVENT, context()).split('\n');
    const around = rows.slice(rows.findIndex(r => r.startsWith('around it')) + 1);
    // Every row of the block is a line (marked or not) or a continuation
    // indented under one: a row at the margin reads as a line of its own.
    expect(around.filter(r => !r.startsWith(`${EVENT_MARK} `) && !r.startsWith('   '))).toEqual([]);
    const traceback = around.findIndex(r => r.includes('Traceback'));
    expect(around[traceback - 1].startsWith(EVENT_MARK)).toBe(true);
    expect(around[traceback + 1]).toMatch(/^ {6,}File "mpv.py"/);
  });

  it('says a source read now is not the one at that moment', () => {
    const then = correctionText(EVENT, context());
    const now = correctionText(EVENT, context({
      source_state: { basis: 'current', source: 'spotify', at: null, detail: 'no transition is logged' },
    }));
    expect(then).toMatch(/source at that moment: radio/);
    expect(now).not.toMatch(/source at that moment/);
    expect(now).toMatch(/source NOW, not known for that moment: spotify/);
  });

  it('names the source a failed transition left selected, failed', () => {
    const text = correctionText(EVENT, context({
      source_state: {
        basis: 'failed_transition', source: 'tidal', at: '2026-11-27T09:59:00Z',
        detail: 'the last transition logged before this moment failed',
      },
    }));
    expect(text).toMatch(/source at that moment: tidal, failed/);
  });

  it('quotes the event as the context redacted it, beside its neighbors', () => {
    // The page's copy and the context's went through two Redactors: a device
    // found by its shape may be bt-3 in one and bt-1 in the other.
    const text = correctionText(
      { ...EVENT, sample: 'Device connected: bt-3' },
      context({ sample: 'Device connected: bt-1' }),
    );
    const event = text.split('\n').slice(0, text.split('\n').findIndex(r => r.startsWith('around it')));
    expect(event).toContain('  Device connected: bt-1');
    expect(text).not.toContain('bt-3');
  });

  it('says nothing was selected after a backend start', () => {
    const text = correctionText(EVENT, context({
      source_state: { basis: 'backend_started', source: null, at: '2026-11-27T09:59:00Z', detail: 'the backend started' },
    }));
    expect(text).toMatch(/source at that moment: none/);
  });

  it('carries the release, the occurrences and what could not be read', () => {
    const text = correctionText(EVENT, context({
      not_collected: [{ source: 'speaker client-2', reason: 'offline in the registry, not asked' }],
    }));
    expect(text).toContain(`first ${EVENT.first_at}, last ${EVENT.last_at}`);
    expect(text).toMatch(/not collected:\n {2}speaker client-2: offline/);
  });

  it('names what it does not know rather than leaving a blank', () => {
    const text = correctionText({ ...EVENT, count: 1 }, context({ build: {}, lines: [], event_index: null }));
    expect(text).toContain('version: unknown');
    expect(text).toContain('commit: unknown');
    expect(text).toContain(`seen once, at ${EVENT.last_at}`);
    expect(marked(text)).toEqual([]);
  });
});
