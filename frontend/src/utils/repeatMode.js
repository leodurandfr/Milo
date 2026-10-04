/**
 * The repeat button of any source that lists `set_repeat`: one press moves to
 * the next mode (off → the whole list → the current track → off), and the
 * source maps the mode to whatever its player takes.
 */
export const REPEAT_MODES = ['off', 'context', 'track'];

export function nextRepeatMode(mode) {
  const index = REPEAT_MODES.indexOf(mode);
  return REPEAT_MODES[(index + 1) % REPEAT_MODES.length];
}
