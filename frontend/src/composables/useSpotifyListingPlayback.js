import { useSpotifyStore } from '@/stores/spotifyStore';
import { useDetailPlayback } from '@/composables/useDetailPlayback';

/**
 * How a Spotify page plays its listing — a playlist's, an album's, an artist's
 * — and draws its rows and its header's play button: the playlist page and the
 * artist page share it.
 *
 * @param {() => string} uri the listing's uri
 */
export function useSpotifyListingPlayback(uri) {
  const store = useSpotifyStore();

  function rowSong(track) {
    return {
      title: track.title,
      artist: track.artists.map((a) => a.name).join(', '),
      duration: (track.duration_ms || 0) / 1000,
    };
  }

  // The row playing now: this track, played from this list — the same track in
  // another playlist is not this row.
  function isCurrent(track) {
    return track.uri === store.currentTrackUri && store.currentContextUri === uri();
  }

  function play({ skipToUri = null } = {}) {
    return store.playContext(uri(), { skipToUri });
  }

  const header = useDetailPlayback('spotify', () => store.currentContextUri === uri(), () => play());

  return { rowSong, isCurrent, play, header };
}
