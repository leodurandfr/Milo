// frontend/tests/pure/spotifyReleases.test.js
/**
 * utils/spotifyReleases.js writes the line under a release on an artist's
 * discography. A failure here is a card that calls a single an album, or one
 * that reads "undefined" or a dangling " · " where Spotify said nothing.
 */
import { describe, it, expect } from 'vitest';
import { releaseSubtitle } from '@/utils/spotifyReleases';

const t = (key) => ({
  'spotify.album': 'Album',
  'spotify.single': 'Single',
  'spotify.compilation': 'Compilation',
  'spotify.latestRelease': 'Latest release',
})[key];

describe('releaseSubtitle', () => {
  it('reads the year then what the release is', () => {
    expect(releaseSubtitle({ year: '2025', release_type: 'album', latest: false }, t)).toBe('2025 · Album');
  });

  it('says first that the latest release is the latest, then what it is', () => {
    expect(releaseSubtitle({ year: '2026', release_type: 'single', latest: true }, t)).toBe('Latest release · Single');
  });

  it('leaves out what Spotify did not say', () => {
    expect(releaseSubtitle({ year: '2022', release_type: null, latest: false }, t)).toBe('2022');
    expect(releaseSubtitle({ year: '2026', release_type: null, latest: true }, t)).toBe('Latest release · 2026');
    expect(releaseSubtitle({ year: null, release_type: null, latest: false }, t)).toBe('');
  });
});
