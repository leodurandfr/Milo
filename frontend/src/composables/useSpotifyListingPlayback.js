import { useSpotifyStore } from '@/stores/spotifyStore';
import { useDetailPlayback } from '@/composables/useDetailPlayback';
import { trackRowSong } from '@/utils/spotifyTrackRow';

/**
 * How a Spotify page plays its listing — a playlist's, an album's, an artist's
 * — and draws its rows and its header's play button: the playlist page and the
 * artist page share it.
 *
 * @param {() => string} uri the listing's uri
 */
export function useSpotifyListingPlayback(uri) {
  const store = useSpotifyStore();

  const rowSong = trackRowSong;

  // A row is its entry as the list holds it (`listed_uri`): Spotify relinks a
  // track to a market alternative when it streams, and from then on
  // go-librespot describes the row by the alternative (`uri`).

  // The row playing now: this entry, played from this list — the same track in
  // another playlist is not this row. The state names the entry as its list
  // holds it, or by the track's own uri when no play order could be read.
  function isCurrent(track) {
    return [track.listed_uri, track.uri].includes(store.currentTrackUri)
      && store.currentContextUri === uri();
  }

  // The list from the top, or from a row: go-librespot finds the row by the
  // uri the list holds, and plays the list's first track for any other.
  function play(track = null) {
    return store.playContext(uri(), { skipToUri: track?.listed_uri ?? null });
  }

  const header = useDetailPlayback('spotify', () => store.currentContextUri === uri(), () => play());

  return { rowSong, isCurrent, play, header };
}
