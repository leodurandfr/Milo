// frontend/src/utils/visibility.js
// Whether an element is on screen, for what costs only while it is seen.
//
// One IntersectionObserver for every caller: a page can hold a few hundred
// targets (a Spotify home's covers), and one observer computes them all in a
// single pass per frame. Intersection already accounts for every ancestor that
// clips, so a tile scrolled out of its shelf or a view skipped by rendering
// (useCover) reads as not visible.

const callbacks = new WeakMap();
let observer = null;

function sharedObserver() {
  observer ??= new IntersectionObserver((entries) => {
    for (const entry of entries) callbacks.get(entry.target)?.(entry.isIntersecting);
  });
  return observer;
}

/**
 * Calls `onChange(visible)` once the element is first measured, then on every
 * change. Returns the function that stops watching it.
 */
export function observeVisibility(el, onChange) {
  callbacks.set(el, onChange);
  sharedObserver().observe(el);
  return () => {
    callbacks.delete(el);
    observer?.unobserve(el);
  };
}
