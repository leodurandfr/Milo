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
