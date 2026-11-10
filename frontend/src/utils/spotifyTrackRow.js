// frontend/src/utils/spotifyTrackRow.js

/**
 * A Spotify track as TrackRow draws it: its title, its artists' names, its
 * length in seconds. A listing's rows (a playlist, an album, an artist's
 * popular tracks) and the search's songs share it.
 *
 * @param {{ title?: string, artists: Array<{ name?: string }>, duration_ms?: number }} track
 */
export function trackRowSong(track) {
  return {
    title: track.title,
    artist: track.artists.map((a) => a.name).filter(Boolean).join(', '),
    duration: (track.duration_ms || 0) / 1000,
  };
}
