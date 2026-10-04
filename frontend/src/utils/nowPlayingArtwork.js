// frontend/src/utils/nowPlayingArtwork.js
// The one rule for "what is in the cover slot", for the sources whose artwork
// is read from the now-playing record (the AudioPlayerFull family) — and, for
// `artworkFallback`, for every source there is.
//
// Read through composables/usePlayerMetadata by both players — the full player
// and the playing bar of the browser sources — for every source they draw.
// Restating the rule per source or per view is what once let Bluetooth show
// its resolved cover in one view and a generated text avatar in another.
import { musicPlaceholder, podcastPlaceholder } from '@/constants/placeholders';

/**
 * @param {object|null} record - a session, a resume record, or the player's snapshot of one
 * @returns {string} cover URL, or '' when there is none
 */
export function nowPlayingArtwork(record) {
  return record?.artwork || '';
}

/**
 * Whether the backend has announced a cover it is still fetching. Only the CD
 * does, in its details, while the Cover Art Archive answers — seconds
 * normally, over a minute when the archive retries — so the slot veils its
 * placeholder rather than swapping it for the cover a moment later.
 *
 * @param {object|null} state - unifiedAudioStore.systemState
 * @returns {boolean}
 */
export function nowPlayingArtworkPending(state) {
  return state?.details?.kind === 'cd' && state.details.artwork_pending === true;
}

// Sources shipping a static image for the no-cover case. Everything else shows
// its own source glyph — the two are not interchangeable, which is why this is
// a lookup and not a single default.
const FALLBACK_IMAGES = {
  cd: musicPlaceholder,
  music_library: musicPlaceholder,
  podcast: podcastPlaceholder,
  // Drawn by AudioPlayer, which renders an image or an avatar, never a glyph.
  spotify: musicPlaceholder,
};

/**
 * What fills the cover slot when there is no cover at all.
 *
 * Three kinds, and the caller renders whichever it is told:
 *   - `avatar` — radio only, and only radio: a station without a favicon is
 *     drawn as the deterministic SVG avatar generated from its name
 *     (utils/stationAvatar), which is the station's identity rather than a
 *     stand-in. Every other source reaching for that avatar is the bug this
 *     function exists to make impossible — a receiver's track title drawn
 *     full-screen as a generated avatar.
 *   - `image` — the bundled placeholder for that source.
 *   - `glyph` — the source's own AppIcon.
 *
 * @param {string} source - active source id
 * @returns {{kind: 'avatar'} | {kind: 'image', src: string} | {kind: 'glyph'}}
 */
export function artworkFallback(source) {
  if (source === 'radio') return { kind: 'avatar' };
  const src = FALLBACK_IMAGES[source];
  return src ? { kind: 'image', src } : { kind: 'glyph' };
}
