/**
 * The element `el` scrolls in, or null when it scrolls with the page.
 *
 * Whatever measures against "the visible area" needs this box, never the
 * window: the room for the A–Z rail's letters is the scroll container's, an
 * IntersectionObserver's margin reaches nothing that box clips, and
 * `scrollIntoView()` scrolls EVERY scrollable ancestor (an `overflow: hidden`
 * one included — it still scrolls under script), which slid the whole interface
 * up by the offset it wanted and never slid it back.
 *
 * A box that only clips is passed over: `overflow-x: hidden` alone computes
 * its overflow-y to `auto`, and such a box, as tall as what it holds, keeps
 * every sentinel inside it "in reach" — a whole list mounted at once.
 */
export function scrollParentOf(el) {
  for (let node = el?.parentElement; node; node = node.parentElement) {
    const overflowY = getComputedStyle(node).overflowY;
    const scrolls = overflowY === 'auto' || overflowY === 'scroll';
    if (scrolls && node.scrollHeight > node.clientHeight) return node;
  }
  return null;
}
