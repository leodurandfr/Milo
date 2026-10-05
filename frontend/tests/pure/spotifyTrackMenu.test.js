// frontend/tests/pure/spotifyTrackMenu.test.js
/**
 * utils/spotifyTrackMenu.js decides which pages the Spotify browser's ⋯ menu
 * opens. A failure here is an entry that leads nowhere (a radio Spotify did
 * not make), an entry that reopens the page already on screen, or a single's
 * album offered as a page of one track.
 */
import { describe, it, expect } from 'vitest';
import { trackMenuActions, albumKnownToHoldMore, canHaveRadio } from '@/utils/spotifyTrackMenu';

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
    expect(trackMenuActions(TRACK, 'artist', { albumLength: 36, radioUri: RADIO })).toEqual(['radio', 'album']);
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
