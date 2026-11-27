// frontend/tests/pure/logPeriods.test.js
/**
 * The windows the logs page offers, against the journal's retention. If it
 * breaks, the page offers a window past the retention — thirty days read from
 * a journal kept seven, which shows errors.log's rotations alone and passes for
 * a quiet month — or hides a window the retention reaches, or keeps a selected
 * window the retention was shortened under.
 *
 * The retentions are read from the backend (`LOG_RETENTION_DAYS` in
 * config/constants.py), and the windows from the route's own bounds
 * (`LOG_WINDOW_PATTERN`, `LOG_WINDOW_MAX` in api/system.py), so a period added
 * on either side surfaces here. The settings screen's choice is held to the
 * same three values. Mounts nothing.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { LOG_PERIODS, offeredLogPeriods, periodWithin } from '@/utils/logPeriods';

const HERE = dirname(fileURLToPath(import.meta.url));
const BACKEND = resolve(HERE, '../../../backend');
const SRC = resolve(HERE, '../../src');

function backendRetentions() {
  const constants = readFileSync(resolve(BACKEND, 'config/constants.py'), 'utf8');
  const m = constants.match(/^LOG_RETENTION_DAYS = Literal\[([\d, ]+)\]$/m);
  if (!m) throw new Error('LOG_RETENTION_DAYS not found in config/constants.py — the extractor is broken');
  return m[1].split(',').map(Number).sort((a, b) => a - b);
}

function backendWindowMaxDays() {
  const system = readFileSync(resolve(BACKEND, 'api/system.py'), 'utf8');
  const m = system.match(/^LOG_WINDOW_MAX = timedelta\(days=(\d+)\)$/m);
  if (!m) throw new Error('LOG_WINDOW_MAX not found in api/system.py — the extractor is broken');
  return Number(m[1]);
}

const days = period => (period.unit === 'd' ? period.amount : period.amount / 24);
const RETENTIONS = backendRetentions();

describe('the logs page offers windows up to the journal retention', () => {
  it('reads the retentions the backend takes', () => {
    expect(RETENTIONS.length).toBeGreaterThanOrEqual(3);
  });

  it('every window is one the route takes', () => {
    const max = backendWindowMaxDays();
    for (const period of LOG_PERIODS) {
      expect(period.value).toMatch(/^\d{1,4}[mhd]$/);
      expect(period.value).toBe(`${period.amount}${period.unit}`);
      expect(days(period)).toBeLessThanOrEqual(max);
    }
  });

  it.each(RETENTIONS)('a retention of %i days offers every window it reaches, and none past it', (retention) => {
    const offered = offeredLogPeriods(retention);
    expect(offered.map(p => p.value)).toEqual(
      LOG_PERIODS.filter(p => days(p) <= retention).map(p => p.value)
    );
    // The longest window offered is the retention itself.
    expect(days(offered[offered.length - 1])).toBe(retention);
  });

  it('before the first answer, only what the shortest retention reaches', () => {
    expect(offeredLogPeriods(null)).toEqual(offeredLogPeriods(RETENTIONS[0]));
  });

  it('a window the retention no longer reaches falls back to the longest it does', () => {
    const longest = RETENTIONS[RETENTIONS.length - 1];
    const shortest = RETENTIONS[0];
    expect(periodWithin(`${longest}d`, offeredLogPeriods(shortest))).toBe(`${shortest}d`);
    expect(periodWithin('1h', offeredLogPeriods(shortest))).toBe('1h');
    expect(periodWithin(`${longest}d`, offeredLogPeriods(longest))).toBe(`${longest}d`);
  });

  it('the settings screen offers the retentions the backend takes', () => {
    const screen = readFileSync(resolve(SRC, 'components/settings/categories/SystemAdvancedSettings.vue'), 'utf8');
    const m = screen.match(/const retentionOptions = computed\(\(\) => \[([\d, ]+)\]/);
    if (!m) throw new Error('retentionOptions not found — the extractor is broken');
    expect(m[1].split(',').map(Number)).toEqual(RETENTIONS);
  });
});
