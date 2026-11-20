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

/**
 * The volume management a card pick shows, on the main unit (Hardware) and on
 * a speaker (ClientEdit) alike. Back on the saved card — picked again from the
 * list, which answers even for the card already chosen — it is the saved one:
 * the dirty check compares the card alone, so the category default would show
 * a value the unit does not hold, with no Apply to send it, and hide the level
 * trim it gates. Any other card starts from its category's default: a DAC's
 * level belongs to the amp after it.
 */
export function volumeControlForCard({ audioId, card, savedAudioId, savedVolumeControl }) {
  if (savedAudioId && audioId === savedAudioId) return savedVolumeControl;
  return card?.category !== 'dac';
}
