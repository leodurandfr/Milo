// frontend/src/utils/nowPlayingArtwork.js
// The one rule for "what is in the cover slot", for the sources whose artwork
// rides on `systemState.metadata` (the AudioPlayerFull family) — and, for
// `artworkFallback`, for every source there is.
//
// Read by AudioPlayerFull — what the player paints — AND by useScreensaver /
// AudioScreensaver — what the screensaver paints. The two must never disagree:
// the screensaver is a full-screen restatement of the player and crossfades
// into it on dismiss, so two different covers there read as a glitch rather
// than as two views of one track. Restating the rule per source is what let
// Bluetooth show its resolved cover in the player and a generated text avatar
// on the screensaver.
//
// The three browser sources (radio, podcast, music_library) are deliberately
// out of scope for `nowPlayingArtwork`: their artwork comes from their own
// Pinia store, which both AudioPlayer and the screensaver already read — one
// source of truth already. They are *in* scope for `artworkFallback`, which is
// the half that had no shared rule at all.
import { musicPlaceholder, podcastPlaceholder } from '@/constants/placeholders';

/**
 * @param {object|null} metadata - systemState.metadata (or the player's cached copy)
 * @returns {string} cover URL, or '' when there is none
 */
export function nowPlayingArtwork(metadata) {
  return (metadata || {}).album_art_url || '';
}

// Sources shipping a static image for the no-cover case. Everything else shows
// its own source glyph — the two are not interchangeable, which is why this is
// a lookup and not a single default.
const FALLBACK_IMAGES = {
  cd: musicPlaceholder,
  music_library: musicPlaceholder,
  podcast: podcastPlaceholder,
};

/**
 * What fills the cover slot when there is no cover at all.
 *
 * Three kinds, and the caller renders whichever it is told:
 *   - `avatar` — radio only, and only radio: a station without a favicon is
 *     drawn as the deterministic SVG avatar generated from its name
 *     (utils/stationAvatar), which is the station's identity rather than a
 *     stand-in. Every other source reaching for that avatar is the bug this
 *     function exists to make impossible — it is how a DLNA renderer came to
 *     be announced full-screen as the word "DLNA" in a coloured tile.
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
