/**
 * Whether ClientEdit should read a satellite's card list and current card now.
 * Both are read only from a reachable satellite, and again on each return
 * while either is unknown: a panel can open on a satellite that is rebooting,
 * and a failed read of the current card leaves the dirty check (and so Apply)
 * with nothing to compare against. Once both are known a return reads nothing,
 * which is what the reboot after an Apply relies on.
 */
export function needsHardwareRead({ reachable, cardsLoaded, savedAudioId, inFlight }) {
  return reachable && !inFlight && (!cardsLoaded || !savedAudioId);
}
