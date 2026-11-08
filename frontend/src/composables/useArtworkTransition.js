// frontend/src/composables/useArtworkTransition.js
// Keeps the outgoing cover on screen, untouched, until the incoming one has
// decoded, and swaps them the moment it has — so a track or station change
// never flashes the fallback (a source glyph, a placeholder, a generated
// station avatar) between two covers, and never draws a spinner over one.
// Nothing is laid over a cover while it waits: the old one simply stays.
//
// Two waits, both bounded by ARTWORK_WAIT_MS:
//   - waiting for the bytes — a known URL decoding off-screen. It is painted
//     on its `load`, or once the bound runs out, since an image that will not
//     decode fires no event at all;
//   - waiting for a cover the backend announced (`artwork_pending`) — CD
//     publishes the disc first and fetches its jacket after, Bluetooth and
//     radio's in-band titles look theirs up from the track text. A source may
//     publish a stand-in meanwhile (radio: the station logo), which is not
//     headed for: the announcement says it is not the answer yet. The outgoing
//     cover is held instead, but only for the bound — a lookup can last over a
//     minute, and a cover that belongs to the previous track must not sit
//     beside the new title that long. Past it, the slot shows what the record
//     carries now (the stand-in, or the fallback), and the cover the backend
//     answers later replaces that as usual. With no cover on screen there is
//     nothing to hold, and the stand-in is shown at once.
// Nothing else is waited for: a source whose cover comes later says so, so a
// track that arrives with no cover and no announcement has none, and the
// fallback is drawn at once as the answer.
//
// The players fade one cover into the next (`fades`), except at the first
// settle after a mount: what it replaces is the fallback drawn while the cover
// decodes, often before the first paint, and fading that out would show it for
// half a second on every open.
import { nextTick, ref, watch } from 'vue';
import { useTimer } from '@/composables/useTimer';
import { MIN_IMAGE_SIZE } from '@/constants/imageQuality';

// How long the outgoing cover may stay on screen once the track has changed:
// while the next one decodes, or while the backend looks it up.
export const ARTWORK_WAIT_MS = 4000;

/**
 * @param {import('vue').Ref<string>} target - the cover we are heading to ('' when unknown yet)
 * @param {import('vue').Ref<string>} trackKey - changes when the track does, cover or not
 * @param {import('vue').Ref<boolean>} [announced] - the backend says a cover is on
 *   its way. While it is up, `target` is only a stand-in, and what is shown is
 *   held for ARTWORK_WAIT_MS at most.
 * @returns {{
 *   shownArtwork: import('vue').Ref<string>,
 *   preloadArtwork: import('vue').Ref<string>,
 *   fades: import('vue').Ref<boolean>,
 *   settleFromLoad: (event: Event) => void,
 *   settleFromError: () => void
 * }}
 *   The consumer renders `preloadArtwork` in an off-screen <img> and wires these
 *   two straight to its load/error. It does not get to decide what counts as a
 *   cover — see the size rule below. `fades` says whether a change of
 *   `shownArtwork` fades (the shells' `cover-fade` transitions take it as `css`).
 */
export function useArtworkTransition(target, trackKey, announced = ref(false)) {
  const timer = useTimer();

  const shownArtwork = ref('');
  const preloadArtwork = ref('');
  const fades = ref(false);
  let wait = null;
  let holding = false;
  let arming = false;

  // A tick late, so the render that paints the first settle still sees false.
  function armFades() {
    if (arming) return;
    arming = true;
    nextTick(() => { fades.value = true; });
  }

  function disarm() {
    if (wait) { timer.clear(wait); wait = null; }
    holding = false;
  }

  function settleArtwork(url) {
    disarm();
    shownArtwork.value = url;
    preloadArtwork.value = '';
    armFades();
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

  function headFor(url) {
    // Already on screen: the next track of the same album resolves to the cover
    // it is showing. Nothing will load, so a wait armed by the track change has
    // to be settled here or it would replace a correct cover at T+4 s.
    if (url === shownArtwork.value) { settleArtwork(url); return; }
    if (url === preloadArtwork.value) return; // already on its way
    disarm();
    preloadArtwork.value = url;
    // Decode too slow, or no load event at all → paint it anyway.
    wait = timer.setTimeout(() => settleArtwork(url), ARTWORK_WAIT_MS);
  }

  // What the record carries now, while its cover is still announced: the
  // stand-in when there is one, else the fallback.
  function showCurrent() {
    if (target.value) headFor(target.value);
    else settleArtwork('');
  }

  // A cover is announced for a new track: keep the outgoing one, for the bound.
  // A cover the previous track was still decoding is dropped — it belongs to
  // that track, not to the title now beside it. A hold already running is not
  // extended: two quick skips keep the first track's cover 4 s, not 8.
  function holdForAnnounced() {
    if (!shownArtwork.value) { showCurrent(); return; }
    if (holding) return;
    disarm();
    preloadArtwork.value = '';
    holding = true;
    wait = timer.setTimeout(() => { wait = null; holding = false; showCurrent(); }, ARTWORK_WAIT_MS);
  }

  // A new track with no cover. Announced: hold what is shown until the backend
  // answers or the bound runs out. Not announced: none is coming, so the
  // fallback is the answer now — holding the previous cover would show it
  // beside a title it does not belong to.
  watch(trackKey, () => {
    if (announced.value) { holdForAnnounced(); return; }
    if (target.value) return; // the target watch heads for it
    settleArtwork('');
  });

  // The backend answered a cover it announced. Without a URL the placeholder
  // is its answer, now — not when a wait armed by the track change runs out;
  // with one (the cover, or the stand-in a miss leaves), that is where to go.
  watch(announced, (on) => {
    if (on) holdForAnnounced();
    else showCurrent();
  });

  // A cover to head for — arriving with the track, or resolved later. Not a
  // stand-in published while the real one is announced.
  watch(target, (url) => {
    if (!url || announced.value) return;
    headFor(url);
  });

  // Mounted on a track: head for its cover, or — announced — show the stand-in,
  // there being nothing on screen yet to hold. With no cover at all the
  // fallback is the answer already, and a cover arriving later is a change on
  // screen, which fades.
  if (announced.value) showCurrent();
  else if (target.value) headFor(target.value);
  else armFades();

  return { shownArtwork, preloadArtwork, fades, settleFromLoad, settleFromError };
}
