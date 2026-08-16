// frontend/src/composables/useArtworkTransition.js
// Holds the outgoing cover in place, under a veil, until the incoming one has
// decoded — so a track change never flashes the fallback (a source glyph in the
// player, a generated text avatar on the screensaver) between two covers.
//
// Two different waits hide behind one veil, and both are real:
//   - waiting for the URL — Bluetooth's cover is looked up from the track text
//     after the fact, so it lands about a second after the title;
//   - waiting for the bytes — every source still has to decode the bitmap.
//
// Shared by AudioPlayerFull and AudioScreensaver rather than written twice: the
// screensaver is a full-screen restatement of the player and crossfades into it,
// so a transition that behaved differently in each would be visible precisely at
// the moment the two are superimposed.
import { ref, watch } from 'vue';
import { useTimer } from '@/composables/useTimer';
import { MIN_IMAGE_SIZE } from '@/constants/imageQuality';

// The only signal that a cover is never coming: the backend has no "no cover for
// this track" event, and an image that will not decode fires no `load` either.
// Bounded so the veil always lifts.
export const ARTWORK_WAIT_MS = 4000;

/**
 * @param {import('vue').Ref<string>} target - the cover we are heading to ('' when unknown yet)
 * @param {import('vue').Ref<string>} trackKey - changes when the track does, cover or not
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
export function useArtworkTransition(target, trackKey) {
  const timer = useTimer();

  const shownArtwork = ref('');
  const preloadArtwork = ref('');
  const artworkPending = ref(false);
  let wait = null;

  function settleArtwork(url) {
    if (wait) { timer.clear(wait); wait = null; }
    shownArtwork.value = url;
    preloadArtwork.value = '';
    artworkPending.value = false;
  }

  // What counts as a cover, decided once for both views. A tracking pixel or a
  // broken favicon decodes perfectly well and is not a cover, so the verdict has
  // to be shared: the screensaver is superimposed on the player during its leave
  // crossfade, and the two cannot disagree there. Leaving the call to the
  // consumer is exactly how the player came to paint a 1×1 image as a cover
  // while the screensaver replaced it with a generated avatar for the same
  // track — same URL, opposite verdicts, invisible to a parity check on the URL.
  function settleFromLoad(event) {
    const img = event.target;
    const tooSmall = img.naturalWidth < MIN_IMAGE_SIZE || img.naturalHeight < MIN_IMAGE_SIZE;
    settleArtwork(tooSmall ? '' : preloadArtwork.value);
  }

  function settleFromError() {
    settleArtwork('');
  }

  function waitFor(fallbackUrl) {
    artworkPending.value = true;
    if (wait) timer.clear(wait);
    wait = timer.setTimeout(() => settleArtwork(fallbackUrl), ARTWORK_WAIT_MS);
  }

  // A new track whose cover is not known yet: hold the previous one rather than
  // dropping to the fallback, and give up on the fallback if none ever comes.
  watch(trackKey, () => { if (!target.value) waitFor(''); });

  // A cover to head for — arriving with the track, or resolved later.
  watch(target, (url) => {
    if (!url) return;
    // Already on screen: the next track of the same album resolves to the cover
    // it is showing. Nothing will load, so the wait armed by the track change
    // has to be settled here or it would blank a correct cover at T+4 s.
    if (url === shownArtwork.value) { settleArtwork(url); return; }
    preloadArtwork.value = url;
    waitFor(url); // decode too slow, or no load event at all → paint it anyway
  }, { immediate: true });

  return { shownArtwork, preloadArtwork, artworkPending, settleFromLoad, settleFromError };
}
