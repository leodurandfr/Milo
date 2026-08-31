// frontend/tests/pure/nowPlayingArtwork.test.js
/**
 * The two cover rules shared by AudioPlayer, AudioPlayerFull and the
 * screensaver: which URL is the cover, and what fills the slot when there is
 * none.
 *
 * Only the branches that decide something are asserted — handing the helper an
 * album_art_url and checking it comes back would assert the language, not the
 * rule. What matters is that the no-cover answer is *per source*: the generated
 * station avatar belongs to radio alone (a DLNA renderer announced full-screen
 * as the word "DLNA" is what a global answer produced), the bundled disc and
 * microphone belong to the sources that ship them, and everyone else falls to
 * their own glyph.
 */
import { describe, it, expect } from 'vitest';
import { nowPlayingArtwork, artworkFallback } from '@/utils/nowPlayingArtwork';

describe('nowPlayingArtwork', () => {
  it('reports no cover as the empty string, not as undefined', () => {
    // '' is what routes the caller into artworkFallback below; undefined would
    // render an <img> with no src, which reads as a broken image.
    expect(nowPlayingArtwork({ title: 'Says', artist: 'Nils Frahm' })).toBe('');
  });

  it('survives the metadata being absent entirely', () => {
    // The screensaver reads this during transitions, when the store's metadata
    // is briefly null — a throw there blanks the whole screen.
    expect(nowPlayingArtwork(null)).toBe('');
  });
});

describe('artworkFallback', () => {
  it('gives the generated station avatar to radio and to nothing else', () => {
    expect(artworkFallback('radio')).toEqual({ kind: 'avatar' });

    for (const source of ['dlna', 'qobuz', 'bluetooth', 'airplay', 'spotify', 'tidal', 'mac']) {
      expect(artworkFallback(source).kind).not.toBe('avatar');
    }
  });

  it('ships a disc for the musical sources and a microphone for podcasts', () => {
    const music = artworkFallback('music_library');
    const cd = artworkFallback('cd');
    const podcast = artworkFallback('podcast');

    expect(music.kind).toBe('image');
    // CD and the library are the same silence, so they are the same drawing —
    // they were two files, and the two drifted apart in format and in ground.
    expect(cd).toEqual(music);

    expect(podcast.kind).toBe('image');
    expect(podcast.src).not.toBe(music.src);
  });

  it('sends every remaining source to its own glyph', () => {
    // The receivers and the connect players: their identity is the source, not
    // a stand-in cover, and AudioPlayerFull already paints exactly that.
    for (const source of ['spotify', 'tidal', 'bluetooth', 'airplay', 'dlna', 'qobuz', 'mac']) {
      expect(artworkFallback(source)).toEqual({ kind: 'glyph' });
    }
  });
});
