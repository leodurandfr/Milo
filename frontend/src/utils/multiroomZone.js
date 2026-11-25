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
