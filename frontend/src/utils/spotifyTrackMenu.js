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
 * The track's artists the menu leads to: each one Spotify names a page for,
 * but the artist whose page the row is on (`pageUri`).
 *
 * @param {{ artists: Array<{name?: string, uri?: string}> }} track
 * @param {string|null} pageUri
 */
export function menuArtists(track, pageUri = null) {
  return track.artists.filter((artist) => artist.uri && artist.uri !== pageUri);
}

/**
 * The pages a row's menu opens, in Spotify's own order: the song radio, the
 * artists', and the album's. A page is left out where the row already is
 * (`kind`, and on an artist's page its `pageUri`), or when nothing leads to
 * it: the track names no uri for it, the album is not known to hold more than
 * this track (`albumLength`, null when not known), or Spotify gave no radio
 * (`radioUri`).
 *
 * @param {{ uri: string, artists: Array<{uri?: string}>, album?: {uri?: string},
 *   track_number?: number, disc_number?: number }} track
 * @param {'playlist'|'liked'|'album'|'artist'} kind The page the row is on.
 * @param {{ albumLength?: number|null, radioUri?: string|null, pageUri?: string|null }} answers
 * @returns {Array<'radio'|'artist'|'album'>}
 */
export function trackMenuActions(track, kind, { albumLength = null, radioUri = null, pageUri = null } = {}) {
  const actions = [];
  if (radioUri) actions.push('radio');
  if (menuArtists(track, pageUri).length) actions.push('artist');
  if (kind !== 'album' && track.album?.uri && (albumKnownToHoldMore(track) || albumLength > 1)) {
    actions.push('album');
  }
  return actions;
}
