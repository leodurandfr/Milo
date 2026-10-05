// frontend/src/utils/spotifyTrackMenu.js
// What a track row's ⋯ menu offers in the Spotify browser.

/**
 * Whether the track itself says its album holds more than it: a track past
 * the first, or on a second disc. A first track says nothing — a single's
 * album is one track — so its album's length is asked for.
 */
export function albumKnownToHoldMore(track) {
  return (track.track_number ?? 0) > 1 || (track.disc_number ?? 0) > 1;
}

/** Whether Spotify can make a radio from the track: a local file in a
 * playlist is not one it serves. */
export function canHaveRadio(track) {
  return track.uri.startsWith('spotify:track:');
}

/**
 * The pages a row's menu opens, in order: the artist's, the album's, and the
 * song radio. A page is left out where the row already is, or when nothing
 * leads to it: the track names no uri for it, the album is not known to hold
 * more than this track (`albumLength`, null when not known), or Spotify gave
 * no radio (`radioUri`).
 *
 * @param {{ uri: string, artists: Array<{uri?: string}>, album?: {uri?: string},
 *   track_number?: number, disc_number?: number }} track
 * @param {'playlist'|'liked'|'album'|'artist'} kind The page the row is on.
 * @param {{ albumLength?: number|null, radioUri?: string|null }} answers
 * @returns {Array<'artist'|'album'|'radio'>}
 */
export function trackMenuActions(track, kind, { albumLength = null, radioUri = null } = {}) {
  const actions = [];
  if (kind !== 'artist' && track.artists[0]?.uri) actions.push('artist');
  if (kind !== 'album' && track.album?.uri && (albumKnownToHoldMore(track) || albumLength > 1)) {
    actions.push('album');
  }
  if (radioUri) actions.push('radio');
  return actions;
}
