/**
 * What a zone row can draw for its level, from its members. The server
 * averages only the members it can reach that have volume control, and a zone
 * where none does is published at DEFAULT_VOLUME_DB, a level nobody set and
 * that no move changes: such a row draws a label, never a slider.
 *
 * - 'counted': a reachable member has volume control, the average is real;
 * - 'external': every reachable member is a DAC, its amplifier owns the level;
 * - 'offline': no member is reachable, which outranks being a DAC.
 */
export function zoneLevelState(members) {
  const online = (members ?? []).filter(member => member.online);
  if (online.length === 0) return 'offline';
  return online.some(member => member.volume_control !== false) ? 'counted' : 'external';
}

/**
 * Whether a member leaving `zone` deletes it. A zone holds at least two
 * members (backend Zone.is_valid), so the registry deletes one that would fall
 * below — its name and its crossover with it — and the member left plays on
 * its own. ClientEdit names that zone in the confirmation of a delete.
 */
export function leavingDeletesZone(zone) {
  return (zone?.client_ids?.length ?? 0) === 2;
}

/**
 * Where a zone's members land when its slider aims their average at
 * `targetDb`: the backend's move (VolumeConfig.delta_to_average, then
 * move_level on each member), so a zone drag can move its members' thumbs
 * with it instead of leaving them a step behind the server's echoes.
 * `levels` maps each member the average counts (reachable, with volume
 * control) to its level, and the answer maps the same members. Every member
 * moves by one delta and stops at the limits; the average is linear between
 * the deltas at which a member meets a limit, so walking those, nearest
 * first, gives the exact delta. Held to the backend by
 * backend/tests/contracts/zone_move_cases.json.
 */
export function moveZoneTo(levels, targetDb, { min, max }) {
  const clamp = (db) => Math.min(Math.max(db, min), max);
  const values = Object.values(levels);
  if (values.length === 0) return {};
  const averageAfter = (delta) => values.reduce((sum, db) => sum + clamp(db + delta), 0) / values.length;
  const moved = (delta) => Object.fromEntries(
    Object.entries(levels).map(([macId, db]) => [macId, clamp(db + delta)]),
  );

  const target = clamp(targetDb);
  const average = averageAfter(0);
  if (target === average) return moved(0);
  const down = target < average;
  const limit = down ? min : max;
  const stops = [...new Set(values.map((db) => limit - db))]
    .filter((stop) => (down ? stop < 0 : stop > 0))
    .sort((a, b) => Math.abs(a) - Math.abs(b));

  let previousDelta = 0;
  let previousAverage = average;
  for (const stop of stops) {
    const reached = averageAfter(stop);
    if (down ? reached <= target : reached >= target) {
      return moved(previousDelta
        + (target - previousAverage) * (stop - previousDelta) / (reached - previousAverage));
    }
    previousDelta = stop;
    previousAverage = reached;
  }
  return moved(previousDelta);
}
