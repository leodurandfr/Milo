// frontend/src/utils/nowPlayingMetadata.js
// What a now-playing view names, read the same way by AudioPlayerFull and the
// screensaver so the two cannot draw different tracks.

/**
 * The record the view of `source` draws: the session, or — with none — what
 * play would bring back (a CD's track, a library resume). Null when the state
 * belongs to another source: its session is not ours to draw.
 *
 * @param {object|null} state - unifiedAudioStore.systemState
 * @param {string} source - the source whose view asks
 * @returns {object|null} a session or a resume record (same title/artist/artwork fields)
 */
export function nowPlayingOf(state, source) {
  if (!state || state.source !== source) return null;
  return state.session ?? state.resume ?? null;
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
