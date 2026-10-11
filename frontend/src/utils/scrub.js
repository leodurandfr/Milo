// frontend/src/utils/scrub.js
// Where a press or a drag on the progress bar lands, in milliseconds. The
// pointer is captured for the length of a drag, so it can leave the bar on
// either side: past an end it holds that end rather than aiming outside the
// track.

/**
 * @param {number} clientX - the pointer, in viewport pixels
 * @param {number} left - the track's left edge, in viewport pixels
 * @param {number} width - the track's width, in pixels
 * @param {number} duration - the track's length, in milliseconds
 * @returns {number} the position under the pointer, in whole milliseconds
 */
export function seekPositionAt(clientX, left, width, duration) {
  if (!(width > 0) || !(duration > 0)) return 0;
  const fraction = Math.min(1, Math.max(0, (clientX - left) / width));
  return Math.floor(duration * fraction);
}
