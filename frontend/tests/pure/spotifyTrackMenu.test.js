// frontend/tests/pure/spotifyTrackMenu.test.js
/**
 * utils/spotifyTrackMenu.js decides which pages the Spotify browser's ⋯ menu
 * opens. A failure here is an entry that leads nowhere (a radio Spotify did
 * not make), an entry that reopens the page already on screen, or a single's
 * album offered as a page of one track.
 */
import { describe, it, expect } from 'vitest';
import { trackMenuActions, trackEditActions, menuArtists, albumKnownToHoldMore, canHaveRadio } from '@/utils/spotifyTrackMenu';

const TRACK = {
  uri: 'spotify:track:0yNttAVwMr39qyODHNIkrY',
  artists: [{ uri: 'spotify:artist:0gxyHStUsqpMadRV0Di1Qt' }],
  album: { uri: 'spotify:album:4C4LvbYS0pxXLW5sGD9EK5' },
  track_number: 1,
  disc_number: 1,
};
const RADIO = 'spotify:playlist:37i9dQZF1E8M6tDY4CZ7kr';

describe('trackMenuActions', () => {
  it('offers the radio, the artist and the album from a playlist, in that order', () => {
    expect(trackMenuActions(TRACK, 'playlist', { albumLength: 36, radioUri: RADIO })).toEqual(['radio', 'artist', 'album']);
  });

  it('leaves out the page the row is already on', () => {
    expect(trackMenuActions(TRACK, 'album', { albumLength: 36, radioUri: RADIO })).toEqual(['radio', 'artist']);
    const pageUri = TRACK.artists[0].uri;
    expect(trackMenuActions(TRACK, 'artist', { albumLength: 36, radioUri: RADIO, pageUri })).toEqual(['radio', 'album']);
  });

  it("leaves out a single's album, and an album whose length is not known", () => {
    expect(trackMenuActions(TRACK, 'playlist', { albumLength: 1, radioUri: RADIO })).toEqual(['radio', 'artist']);
    expect(trackMenuActions(TRACK, 'playlist', { radioUri: RADIO })).toEqual(['radio', 'artist']);
  });

  it('offers the album of a track past the first without its length', () => {
    expect(trackMenuActions({ ...TRACK, track_number: 4 }, 'playlist')).toContain('album');
    expect(trackMenuActions({ ...TRACK, disc_number: 2 }, 'playlist')).toContain('album');
  });

  it('leaves out the radio Spotify did not make', () => {
    expect(trackMenuActions(TRACK, 'playlist', { albumLength: 36 })).toEqual(['artist', 'album']);
  });

  it('leaves out a page the track names no uri for', () => {
    const bare = { ...TRACK, artists: [], album: {} };
    expect(trackMenuActions(bare, 'playlist', { albumLength: 36, radioUri: RADIO })).toEqual(['radio']);
  });
});

describe('trackEditActions', () => {
  const PLAYLIST = 'spotify:playlist:1mineabcdefghijklmnopq';
  const ROW = { ...TRACK, listed_uri: TRACK.uri };

  it('offers to add any track, and to remove it only from a list the account may edit', () => {
    expect(trackEditActions(ROW)).toEqual(['addToPlaylist']);
    expect(trackEditActions(ROW, PLAYLIST)).toEqual(['addToPlaylist', 'removeFromPlaylist']);
  });

  it('never offers a local file, which go-librespot cannot add or remove', () => {
    const local = { uri: 'spotify:local:Artist:Album:Title:200', listed_uri: 'spotify:local:Artist:Album:Title:200' };
    expect(trackEditActions(local, PLAYLIST)).toEqual([]);
  });
});

describe('menuArtists', () => {
  const DUET = {
    ...TRACK,
    artists: [
      { name: 'Nils Frahm', uri: 'spotify:artist:nils' },
      { name: 'Local', uri: null },
      { name: 'Ólafur Arnalds', uri: 'spotify:artist:olafur' },
    ],
  };

  it('leads to every artist Spotify names a page for, in the line\'s order', () => {
    expect(menuArtists(DUET).map((artist) => artist.name)).toEqual(['Nils Frahm', 'Ólafur Arnalds']);
  });

  it("still offers a featured artist on the page of the track's first one", () => {
    expect(menuArtists(DUET, 'spotify:artist:nils').map((artist) => artist.name)).toEqual(['Ólafur Arnalds']);
    expect(trackMenuActions(DUET, 'artist', { pageUri: 'spotify:artist:nils' })).toEqual(['artist']);
  });
});

describe('albumKnownToHoldMore', () => {
  it('knows from a track past the first or a second disc, never from a first track', () => {
    expect(albumKnownToHoldMore({ track_number: 2, disc_number: 1 })).toBe(true);
    expect(albumKnownToHoldMore({ track_number: 1, disc_number: 2 })).toBe(true);
    expect(albumKnownToHoldMore({ track_number: 1, disc_number: 1 })).toBe(false);
    expect(albumKnownToHoldMore({ track_number: null, disc_number: null })).toBe(false);
  });
});

describe('canHaveRadio', () => {
  it('asks a radio only of a track Spotify serves, never of a local file', () => {
    expect(canHaveRadio(TRACK)).toBe(true);
    expect(canHaveRadio({ uri: 'spotify:local:Artist:Album:Title:200' })).toBe(false);
  });
});
