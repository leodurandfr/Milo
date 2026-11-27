// frontend/src/utils/logPeriods.js
// The windows the logs page offers, back from now, and which of them the
// journal's retention leaves worth offering.
//
// The server keeps its journal 7, 15 or 30 days (`system.log_retention_days`,
// carried by GET /api/system/logs as `retention_days`). A window longer than
// that reads errors.log's rotations alone past the retention, which would pass
// for a journal that was quiet: so a longer window is not offered.

const HOURS_PER_DAY = 24;

// As GET /api/system/logs spells them (`since`), with their length to name.
export const LOG_PERIODS = [
  { value: '1h', amount: 1, unit: 'h' },
  { value: '6h', amount: 6, unit: 'h' },
  { value: '24h', amount: 24, unit: 'h' },
  { value: '7d', amount: 7, unit: 'd' },
  { value: '15d', amount: 15, unit: 'd' },
  { value: '30d', amount: 30, unit: 'd' },
];

// The shortest retention there is: offered before the first answer says which
// one is in force.
const ALWAYS_OFFERED_DAYS = 7;

function lengthInDays(period) {
  return period.unit === 'd' ? period.amount : period.amount / HOURS_PER_DAY;
}

/**
 * The periods offered for a retention of `retentionDays` (null while unknown):
 * every one no longer than it, and up to 7 days whatever it is.
 */
export function offeredLogPeriods(retentionDays) {
  const reach = Math.max(retentionDays ?? ALWAYS_OFFERED_DAYS, ALWAYS_OFFERED_DAYS);
  return LOG_PERIODS.filter(period => lengthInDays(period) <= reach);
}

/**
 * `selected` when it is offered, else the longest period offered: a window
 * the retention no longer reaches falls back to the longest one it does.
 */
export function periodWithin(selected, offered) {
  if (offered.some(period => period.value === selected)) return selected;
  return offered[offered.length - 1].value;
}
