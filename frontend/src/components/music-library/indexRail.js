// Geometry and plumbing for the Artists tab's A–Z rail, kept out of the
// component so the rule the interaction rests on can be asserted directly: the
// letter under the finger is the letter printed there. The rail is abridged to
// fit a short band (an even sample of the index, both ends kept), and the
// pointer is then mapped over that SAME abridged strip — map it over the full
// index instead and a press lands up to a rung away from the letter it touched,
// which is how tapping M scrolled to L.

/**
 * The letters to draw for `letters` in `slots` letter-height rows: the list
 * itself when it fits, otherwise an even sample of it. Never returns more than
 * slots — the rail does not scroll.
 *
 * Nothing marks the letters left out (no interpunct between survivors, as iOS
 * draws): a rail this short skips at most every other letter, and spending half
 * the rows on markers is what forced the harsh abridgement they were marking.
 */
export function condenseLetters(letters, slots) {
  const n = letters?.length || 0;
  // No height measured yet: drawing an unscaled strip would flash a rail that
  // overflows its cap on the first frame.
  if (!n || !(slots > 0)) return [];
  if (slots >= n) return [...letters];
  if (slots === 1) return [letters[0]];

  const out = [];
  for (let i = 0; i < slots; i += 1) {
    out.push(letters[Math.round((i * (n - 1)) / (slots - 1))]);
  }
  return out;
}

/**
 * The letter at a vertical fraction of the rail, in equal bands over the full
 * list — the rail's height is its rows', so a band IS the row drawn there.
 * Out-of-range fractions clamp to the ends: a finger that slides off the top or
 * bottom keeps scrubbing rather than dropping the gesture.
 */
export function letterAtRatio(letters, ratio) {
  const n = letters?.length || 0;
  if (!n || !Number.isFinite(ratio)) return null;
  const i = Math.floor(ratio * n);
  return letters[Math.min(n - 1, Math.max(0, i))];
}

/**
 * The element `el` scrolls in, or null.
 *
 * The rail needs it twice, and both times because the alternative moves the
 * wrong box: measuring the room for letters against `window.innerHeight` counts
 * screen pixels, while the app is laid out in the fewer ones the kiosk's
 * ui_scale transform then magnifies — and `scrollIntoView()` scrolls EVERY
 * scrollable ancestor, `#app` included (`overflow: hidden` still scrolls under
 * script), which slid the whole interface up by the offset it wanted and never
 * slid it back. Both are answered by the one container the view actually
 * scrolls in, whose own metrics are in the space everything else is laid out in.
 */
export function scrollParentOf(el) {
  for (let node = el?.parentElement; node; node = node.parentElement) {
    const overflowY = getComputedStyle(node).overflowY;
    if (overflowY === 'auto' || overflowY === 'scroll') return node;
  }
  return null;
}

/**
 * `el`'s top in the laid-out pixels of `ancestor`, summed up the offsetParent
 * chain rather than taken from a bounding rect: both operands are then in the
 * same space as scrollTop, so the kiosk's ui_scale never enters the arithmetic.
 */
export function offsetWithin(el, ancestor) {
  let top = 0;
  for (let node = el; node && node !== ancestor; node = node.offsetParent) top += node.offsetTop;
  return top;
}
