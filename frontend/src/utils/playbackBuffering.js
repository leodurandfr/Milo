// frontend/src/utils/playbackBuffering.js
/**
 * Is the active source spinning up rather than actually playing?
 *
 * Read by the two full-transport players — AudioPlayerFull and the Lyrics
 * playback bar — which both show a spinner in place of the pause button while
 * this holds. They mount only for the active source, so the caller has already
 * gated on it; this reads the metadata as given.
 *
 * `is_buffering` is the general signal every source reports. CD adds a second
 * window: the player is on screen as soon as the drive reports a disc, but the
 * disc identity (MusicBrainz lookup) can still be in flight, so there is no
 * disc_id/cache_ready yet and nothing is playable. Same rule as
 * AudioSourceView's 'loading_disc'. A fallback DiscInfo always sets disc_id,
 * so the window cannot hang.
 *
 * @param {string} source - Active audio source id
 * @param {Object} metadata - unifiedAudioStore.systemState.metadata
 * @returns {boolean}
 */
export function isSourceBuffering(source, metadata) {
  const meta = metadata || {};
  if (meta.is_buffering) return true;
  return source === 'cd' && !!meta.disc_present && (!meta.cache_ready || !meta.disc_id);
}
