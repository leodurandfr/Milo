/**
 * Spotify's repeat button: one press moves to the next mode, the way the
 * Spotify app's does (off → the whole context → the current track → off).
 * The backend maps a mode to go-librespot's two flags.
 */
export const REPEAT_MODES = ['off', 'context', 'track'];

export function nextRepeatMode(mode) {
  const index = REPEAT_MODES.indexOf(mode);
  return REPEAT_MODES[(index + 1) % REPEAT_MODES.length];
}
