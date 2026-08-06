// frontend/src/utils/nowPlayingArtwork.js
// The one rule for "which cover is on screen", for the sources whose artwork
// rides on `systemState.metadata` (the AudioPlayerFull family).
//
// Read by AudioPlayerFull — what the player paints — AND by useScreensaver —
// what the screensaver paints. The two must never disagree: the screensaver is
// a full-screen restatement of the player and crossfades into it on dismiss, so
// two different covers there read as a glitch rather than as two views of one
// track. Restating the rule per source is what let Bluetooth show its resolved
// cover in the player and a generated text avatar on the screensaver.
//
// The three browser sources (radio, podcast, music_library) are deliberately
// out of scope: their artwork comes from their own Pinia store, which both
// AudioPlayer and the screensaver already read — one source of truth already.
import cdPlaceholder from '@/assets/cd/cd-placeholder.jpg';

// Sources shipping a static image for the no-cover case. Everything else falls
// through to the caller's own fallback — the player's source glyph, the
// screensaver's generated avatar — which are not interchangeable, hence '' here
// rather than a shared default.
const PLACEHOLDERS = { cd: cdPlaceholder };

/**
 * @param {string} source - active source id
 * @param {object|null} metadata - systemState.metadata (or the player's cached copy)
 * @returns {string} cover URL, or '' when there is none
 */
export function nowPlayingArtwork(source, metadata) {
  return (metadata || {}).album_art_url || PLACEHOLDERS[source] || '';
}
