import { useSpotifyStore } from '@/stores/spotifyStore';

/**
 * How a Spotify page plays its listing — a playlist's, an album's, an artist's
 * — and draws its rows: the playlist page and the artist page share it.
 *
 * @param {() => string} uri the listing's uri
 * @param {import('vue').Ref<Array>} tracks the tracks described so far
 */
export function useSpotifyListingPlayback(uri, tracks) {
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

  function play({ skipToUri = null, shuffle = false } = {}) {
    store.playContext(uri(), { skipToUri, shuffle });
  }

  // The first track of a shuffled play is picked here, from the tracks described
  // so far (the whole listing once complete; the order after it is the daemon's,
  // over all of it): go-librespot starts a context from a signed-in idle state
  // with its shuffle off, so it cannot be left to pick (measured).
  function shufflePlay() {
    // A local file in a playlist lists here but cannot be played from Milō.
    const list = tracks.value.filter((track) => !track.uri.startsWith('spotify:local:'));
    if (!list.length) return;
    const start = list[Math.floor(Math.random() * list.length)];
    play({ skipToUri: start.uri, shuffle: true });
  }

  return { rowSong, isCurrent, play, shufflePlay };
}
