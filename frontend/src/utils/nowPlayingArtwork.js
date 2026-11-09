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
 * Whether the backend has announced a cover it is still fetching. A source
 * that finds its cover after it published the track says so in its details,
 * `artwork_pending`, until the answer is in, found or not: the CD while the
 * Cover Art Archive answers (seconds, over a minute when it retries),
 * Bluetooth and radio's in-band titles while iTunes is asked from the track
 * text. The slot holds what it shows, a few seconds at most, rather than
 * drawing the fallback a moment before the cover. One rule for every kind: a
 * source that starts announcing needs no entry here.
 *
 * @param {object|null} state - unifiedAudioStore.systemState
 * @returns {boolean}
 */
export function nowPlayingArtworkPending(state) {
  return state?.details?.artwork_pending === true;
}

/**
 * What fills the cover slot when there is no cover at all.
 *
 * Two kinds, and the caller renders whichever it is told:
 *   - `avatar` — radio only, and only radio: a station without a favicon is
 *     drawn as the deterministic SVG avatar generated from its name
 *     (utils/stationAvatar), which is the station's identity rather than a
 *     stand-in. Every other source reaching for that avatar is the bug this
 *     function exists to make impossible — a receiver's track title drawn
 *     full-screen as a generated avatar. A station with no name to draw has
 *     no avatar either, and gets the disc rather than an empty slot.
 *   - `image` — a bundled placeholder: the microphone for a podcast episode,
 *     the disc for everything else, receivers included. The source's own icon
 *     is not a cover: the source bar under the slot already names the source,
 *     and a missing cover reads the same whichever source it is missing from.
 *
 * @param {string} source - active source id
 * @param {string} [avatarName] - the name the station avatar would be drawn from
 * @returns {{kind: 'avatar'} | {kind: 'image', src: string}}
 */
export function artworkFallback(source, avatarName) {
  if (source === 'radio' && avatarName) return { kind: 'avatar' };
  return { kind: 'image', src: source === 'podcast' ? podcastPlaceholder : musicPlaceholder };
}
