/**
 * dB -> percentage of a meter's span, linear. The span is the caller's: a
 * default would be a second declaration of a range that belongs to the meter
 * (LevelMeter draws -60 → 0 dBFS).
 */
export function dbToPercent(db, min, max) {
  const clamped = Math.max(min, Math.min(max, db));
  return Math.round(((clamped - min) / (max - min)) * 100);
}
