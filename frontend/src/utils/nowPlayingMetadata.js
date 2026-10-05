// frontend/src/utils/nowPlayingMetadata.js
// What a now-playing view names: the record AudioPlayerFull draws, and the
// snapshot it holds on to while that record moves.

/**
 * What another device of the account plays (Spotify's `details.remote`), as a
 * record shaped like a session: nothing of it plays here, and the play button
 * its source lists (`take_over`) brings it here. Null without one — the
 * backend publishes none while a session runs here.
 *
 * @param {object|null} details - the source's details
 * @returns {object|null} title/artist/album/artwork, duration_ms, position and phase
 */
export function remoteRecordOf(details) {
  const remote = details?.remote;
  if (!remote) return null;
  return {
    title: remote.title,
    artist: remote.artist,
    album: remote.album,
    artwork: remote.artwork,
    duration_ms: remote.duration_ms,
    position: remote.position,
    phase: remote.paused ? 'paused' : 'playing',
  };
}

/**
 * The record the view of `source` draws: the session, or — with none — what
 * play would bring back (a CD's track, a library resume, what another device
 * of the account plays). Null when the state belongs to another source: its
 * session is not ours to draw.
 *
 * @param {object|null} state - unifiedAudioStore.systemState
 * @param {string} source - the source whose view asks
 * @returns {object|null} a session, a resume or a remote record (same title/artist/artwork fields)
 */
export function nowPlayingOf(state, source) {
  if (!state || state.source !== source) return null;
  return state.session ?? state.resume ?? remoteRecordOf(state.details);
}

/**
 * The snapshot AudioPlayerFull holds on to, or null when the record is not
 * worth keeping and the previous snapshot should stand — a session with
 * nothing to name (a sender still connecting), or the record gone while the
 * player leaves.
 *
 * A title is the whole requirement: a disc no lookup identified has a real
 * "Track N" title and no artist, and the player is on screen for it.
 *
 * @param {object|null} record - from nowPlayingOf
 * @returns {{title: string, artist: string, artwork: string}|null}
 */
export function nowPlayingSnapshot(record) {
  if (!record?.title) return null;
  return {
    title: record.title,
    artist: record.artist || '',
    artwork: record.artwork || '',
  };
}
