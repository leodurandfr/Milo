// frontend/tests/pure/nowPlayingArtwork.test.js
/**
 * The cover-selection rule shared by AudioPlayerFull and the screensaver.
 *
 * Only the branches that decide something are asserted — handing the helper an
 * album_art_url and checking it comes back would assert the language, not the
 * rule. What matters is which source gets a static placeholder when there is no
 * cover, and that the answer is per-source rather than global: a Bluetooth track
 * with no resolved cover must reach its own glyph fallback, not a CD sleeve.
 */
import { describe, it, expect } from 'vitest';
import { nowPlayingArtwork } from '@/utils/nowPlayingArtwork';

describe('nowPlayingArtwork', () => {
  it('gives the CD its disc placeholder when the lookup found no cover', () => {
    const url = nowPlayingArtwork('cd', { title: 'Ain’t No Sunshine' });

    expect(url).toBeTruthy();
    expect(url).toMatch(/cd-placeholder/);
  });

  it('leaves every other source empty rather than borrowing that placeholder', () => {
    // '' is what routes the caller to its own fallback — the player's source
    // glyph, the screensaver's generated avatar. They are not interchangeable,
    // so there is deliberately no shared default here.
    expect(nowPlayingArtwork('bluetooth', { title: 'Says', artist: 'Nils Frahm' })).toBe('');
    expect(nowPlayingArtwork('spotify', {})).toBe('');
  });

  it('prefers a real cover over the placeholder', () => {
    expect(nowPlayingArtwork('cd', { album_art_url: '/api/cd/cover/abc' }))
      .toBe('/api/cd/cover/abc');
  });

  it('survives the metadata being absent entirely', () => {
    // The screensaver reads this during transitions, when the store's metadata
    // is briefly null — a throw there blanks the whole screen.
    expect(nowPlayingArtwork('spotify', null)).toBe('');
    expect(nowPlayingArtwork('cd', null)).toMatch(/cd-placeholder/);
  });
});
