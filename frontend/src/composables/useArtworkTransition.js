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
 *   settleArtwork: (url: string) => void
 * }}
 *   The consumer renders `preloadArtwork` in an off-screen <img> and calls
 *   `settleArtwork` from its load/error handlers — which is also where a
 *   consumer with its own validation (the screensaver rejects tiny images) gets
 *   to reject the cover and settle on '' instead.
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
    if (!url || url === shownArtwork.value) return;
    preloadArtwork.value = url;
    waitFor(url); // decode too slow, or no load event at all → paint it anyway
  }, { immediate: true });

  return { shownArtwork, preloadArtwork, artworkPending, settleArtwork };
}
