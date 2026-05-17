/**
 * Volume dB <-> percentage conversion.
 * Backend emits dB in [MIN_DB, MAX_DB], the Snapcast UI / LevelMeter
 * display percent in [0, 100]. Linear mapping.
 */
export const MIN_DB = -72;
export const MAX_DB = 0;

export function dbToPercent(db) {
  const clamped = Math.max(MIN_DB, Math.min(MAX_DB, db));
  return Math.round(((clamped - MIN_DB) / (MAX_DB - MIN_DB)) * 100);
}

export function percentToDb(percent) {
  const clamped = Math.max(0, Math.min(100, percent));
  return MIN_DB + (clamped / 100) * (MAX_DB - MIN_DB);
}
