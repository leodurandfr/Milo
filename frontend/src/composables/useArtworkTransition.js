// frontend/src/composables/useArtworkTransition.js
// Holds the outgoing cover in place, under a veil, until the incoming one has
// decoded — so a track change never flashes the fallback (a source glyph, a
// placeholder, a generated station avatar) between two covers.
//
// Two different waits hide behind one veil, and both are real:
//   - waiting for a cover the backend announced — CD publishes the disc first
//     and fetches its jacket after, Bluetooth and radio's in-band titles look
//     theirs up from the track text, each flagging `artwork_pending` meanwhile.
//     A source may publish a stand-in until then (radio: the station logo),
//     which is held off like any fallback: the announcement says it is not
//     the answer yet;
//   - waiting for the bytes — every source still has to decode the bitmap.
// Nothing else is waited for: a source whose cover comes later says so, so a
// track that arrives with no cover and no announcement has none, and the
// fallback is drawn at once as the answer.
import { computed, ref, watch } from 'vue';
import { useTimer } from '@/composables/useTimer';
import { MIN_IMAGE_SIZE } from '@/constants/imageQuality';

// How long a cover URL may take to decode before it is painted anyway: an
// image that will not decode fires no `load` at all. Bounded so the veil over
// the bytes always lifts.
export const ARTWORK_WAIT_MS = 4000;

/**
 * @param {import('vue').Ref<string>} target - the cover we are heading to ('' when unknown yet)
 * @param {import('vue').Ref<string>} trackKey - changes when the track does, cover or not
 * @param {import('vue').Ref<boolean>} [announced] - the backend says a cover is on
 *   its way. Not bounded by ARTWORK_WAIT_MS: the backend lifts it itself, found
 *   or not, so the veil lasts exactly as long as the fetch. While it is up, what
 *   is shown stays shown and `target` is only a stand-in.
 * @returns {{
 *   shownArtwork: import('vue').Ref<string>,
 *   preloadArtwork: import('vue').Ref<string>,
 *   artworkPending: import('vue').Ref<boolean>,
 *   settleFromLoad: (event: Event) => void,
 *   settleFromError: () => void
 * }}
 *   The consumer renders `preloadArtwork` in an off-screen <img> and wires these
 *   two straight to its load/error. It does not get to decide what counts as a
 *   cover — see the size rule below.
 */
export function useArtworkTransition(target, trackKey, announced = ref(false)) {
  const timer = useTimer();

  const shownArtwork = ref('');
  const preloadArtwork = ref('');
  const waiting = ref(false);
  // Announced: the backend lifts it with the answer, and the decode wait below
  // takes over from there, so a cover is never veiled past its own load.
  const artworkPending = computed(() => waiting.value || announced.value);
  let wait = null;

  function settleArtwork(url) {
    if (wait) { timer.clear(wait); wait = null; }
    shownArtwork.value = url;
    preloadArtwork.value = '';
    waiting.value = false;
  }

  // What counts as a cover. A tracking pixel or a broken favicon decodes
  // perfectly well and is not a cover. Leaving the call to each consumer is
  // exactly how one view came to paint a 1×1 image as a cover while another
  // replaced it with a generated avatar for the same track — same URL,
  // opposite verdicts, invisible to a parity check on the URL.
  function settleFromLoad(event) {
    const img = event.target;
    const tooSmall = img.naturalWidth < MIN_IMAGE_SIZE || img.naturalHeight < MIN_IMAGE_SIZE;
    settleArtwork(tooSmall ? '' : preloadArtwork.value);
  }

  function settleFromError() {
    settleArtwork('');
  }

  function waitFor(fallbackUrl) {
    waiting.value = true;
    if (wait) timer.clear(wait);
    wait = timer.setTimeout(() => settleArtwork(fallbackUrl), ARTWORK_WAIT_MS);
  }

  function headFor(url) {
    // Already on screen: the next track of the same album resolves to the cover
    // it is showing. Nothing will load, so the wait armed by the track change
    // has to be settled here or it would blank a correct cover at T+4 s.
    if (url === shownArtwork.value) { settleArtwork(url); return; }
    if (url === preloadArtwork.value && waiting.value) return; // already on its way
    preloadArtwork.value = url;
    waitFor(url); // decode too slow, or no load event at all → paint it anyway
  }

  // A new track with no cover. Announced: hold what is shown until the backend
  // answers, with no bound of its own. Not announced: none is coming, so the
  // fallback is the answer now — holding the previous cover would show it,
  // veiled, beside a title it does not belong to.
  watch(trackKey, () => {
    if (target.value || announced.value) return;
    settleArtwork('');
  });

  // The backend answered a cover it announced. Without a URL the placeholder
  // is its answer, now — not when a wait armed by the track change runs out;
  // with one (the cover, or the stand-in a miss leaves), that is where to go.
  watch(announced, (on) => {
    if (on) return;
    if (target.value) headFor(target.value);
    else settleArtwork('');
  });

  // A cover to head for — arriving with the track, or resolved later. Not a
  // stand-in published while the real one is announced.
  watch(target, (url) => {
    if (!url || announced.value) return;
    headFor(url);
  }, { immediate: true });

  return { shownArtwork, preloadArtwork, artworkPending, settleFromLoad, settleFromError };
}
