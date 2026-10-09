// frontend/src/composables/useCover.js
// A view another one covers and that it will be found under again — a source
// under Lyrics, a navigation under its full player. It stays mounted, so going
// back rebuilds nothing, keeps its page and scroll, and refetches nothing.
//
// Once the overlay has finished entering, the view is skipped by rendering
// (`.is-covered`: `content-visibility: hidden`) rather than `display: none`:
// both stop drawing it, but only the first keeps its style and layout, so
// uncovering it costs a paint instead of a rebuild — measured on the Pi on a
// 1 900-node Spotify page, the longest task 260 ms → 115 ms, style 110 → 19 ms,
// layout 37 → 1 ms; and its resize observers do not fire again, since no size
// went through zero. Its IntersectionObservers do: a skipped target intersects
// nothing, so each sees the cover come and go — one that acts on "not
// intersecting" must ignore a target that is not drawn (useScrollToTop). The
// view is uncovered the moment the overlay starts leaving, so the overlay
// fades out over it.
//
// No `inert`: covered, the view is out of reach of taps and focus already;
// while the overlay comes and goes it lies over the whole view, and the focus
// the view held is let go as it arrives. Blink applies `inert` as an inherited
// style, so toggling it restyled the whole page on both edges — measured, a
// collapse 65 → 25 ms, Lyrics closing 90 → 35 ms.
import { computed, nextTick, ref, watch } from 'vue';
import { riseIn } from '@/utils/sourceMotion';

/**
 * Provided by a view that lays an overlay over the views it holds (a ref,
 * true while one is shown), for what a covered view draws outside its own box
 * — the phone's playing bar, teleported to <body>, which a cover cannot hide.
 */
export const UNDER_OVERLAY = Symbol('underOverlay');

/**
 * @param {import('vue').Ref<boolean>} overlay - the overlay is shown
 * @param {{ peek?: import('vue').Ref<boolean>, reveal?: import('vue').Ref<HTMLElement|null> }} [options]
 *   `peek`: the view is drawn again under an overlay still in place (the
 *   phone's pull down). `reveal`: the view's element, whose marked parts rise
 *   as the overlay leaves — unless it was peeked at, already on screen.
 * @returns {{ covered: import('vue').ComputedRef<boolean>, onOverlayEntered: () => void }}
 *   `covered` for the view's `.is-covered`; `onOverlayEntered` for the
 *   overlay's Transition `@after-enter`. An overlay already shown when the view
 *   mounts covers it at once: no enter is coming.
 */
export function useCover(overlay, { peek, reveal } = {}) {
  const entered = ref(overlay.value);
  let peeked = false;
  watch(overlay, (shown) => {
    if (shown) {
      peeked = false;
      if (reveal?.value?.contains(document.activeElement)) document.activeElement.blur();
      return;
    }
    // Only a view that was covered rises: closed before it had fully come in,
    // the overlay left the view on screen under it, and a rise would jump it.
    const wasCovered = entered.value;
    entered.value = false;
    // After the render that drops `.is-covered`, which riseIn() skips.
    if (reveal?.value && !peeked && wasCovered) nextTick(() => riseIn(reveal.value));
  });
  if (peek) {
    // A pull that springs back leaves nothing on screen; one that dismisses
    // ends with the overlay already gone, and the view stays where it was seen.
    watch(peek, (on) => {
      if (on) peeked = true;
      else if (overlay.value) peeked = false;
    });
  }
  const covered = computed(() => entered.value && !peek?.value);
  function onOverlayEntered() {
    if (overlay.value) entered.value = true;
  }
  return { covered, onOverlayEntered };
}
