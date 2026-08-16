// frontend/src/utils/nowPlayingMetadata.js
/**
 * The snapshot AudioPlayerFull holds on to, so the title/artist/cover do not
 * blank out in the gap between two metadata updates — or null when the incoming
 * metadata is not worth keeping and the previous snapshot should stand.
 *
 * A title is the whole requirement. Requiring an artist alongside it is what
 * froze the CD player on its empty seed: a disc MusicBrainz cannot identify
 * (a burned CD, an obscure pressing, or any disc while the unit is offline)
 * comes back from `_build_fallback_disc_info` with artist=None and a perfectly
 * real "Track N" title, and useRichDisplay admits CD on disc_present +
 * cache_ready alone — so the player is on screen with a title it refused to
 * store. The screensaver, which reads the live metadata instead of a snapshot,
 * showed that same title correctly the whole time.
 *
 * The pair is still required everywhere else, and that is not symmetry for its
 * own sake: every other source mounting this player is gated upstream on title
 * AND artist, so a snapshot carrying one without the other there is a
 * half-populated update, and storing it would blank an artist that is known.
 *
 * @param {string} source - Active audio source id
 * @param {Object|null} metadata - unifiedAudioStore.systemState.metadata
 * @returns {{title: string, artist: string, album_art_url: string}|null}
 */
export function nowPlayingSnapshot(source, metadata) {
  const meta = metadata || {};
  if (!meta.title) return null;
  if (!meta.artist && source !== 'cd') return null;

  return {
    title: meta.title,
    artist: meta.artist || '',
    album_art_url: meta.album_art_url || ''
  };
}
