/**
 * Volume dB <-> percentage conversion.
 * Backend emits dB in [min, max], the Snapcast UI / LevelMeter
 * display percent in [0, 100]. Linear mapping.
 *
 * Default range is the Snapcast convention (-72 dB → 0 dB). Pass explicit
 * min/max for components with a different reference range (e.g. LevelMeter
 * uses -60 → 0 for the 0 dBFS audio-level standard).
 */
export const MIN_DB = -72;
export const MAX_DB = 0;

export function dbToPercent(db, min = MIN_DB, max = MAX_DB) {
  const clamped = Math.max(min, Math.min(max, db));
  return Math.round(((clamped - min) / (max - min)) * 100);
}

export function percentToDb(percent, min = MIN_DB, max = MAX_DB) {
  const clamped = Math.max(0, Math.min(100, percent));
  return min + (clamped / 100) * (max - min);
}
