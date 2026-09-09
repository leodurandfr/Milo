// The column count of the square-artwork grids, read off --card-grid-columns.
// A view that has to slice its list (a showcase showing whole rows) cuts it on
// the number the CSS actually lays out: the ladder lives once, in
// design-system.css, and a matchMedia restating its breakpoints here would be a
// second spelling of it that drifts on the first change.
import { ref, onUnmounted, readonly } from 'vue';

// Only reachable when no stylesheet is attached (unit tests) — the token's own
// :root value, so a mount without CSS still renders a plausible row.
const UNSTYLED = 4;

function read() {
  if (typeof window === 'undefined') return UNSTYLED;
  const declared = getComputedStyle(document.documentElement).getPropertyValue('--card-grid-columns');
  return Number.parseInt(declared, 10) || UNSTYLED;
}

const columns = ref(read());
let listenerCount = 0;

function update() {
  columns.value = read();
}

export function useCardGridColumns() {
  // Re-read on subscribe rather than trusting the module-load value: this file
  // can be evaluated before the stylesheet the count comes from is parsed.
  update();

  if (listenerCount === 0 && typeof window !== 'undefined') {
    window.addEventListener('resize', update);
  }
  listenerCount++;

  onUnmounted(() => {
    listenerCount--;
    if (listenerCount === 0 && typeof window !== 'undefined') {
      window.removeEventListener('resize', update);
    }
  });

  return { columns: readonly(columns) };
}
