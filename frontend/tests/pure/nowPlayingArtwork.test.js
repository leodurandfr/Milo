// frontend/tests/pure/nowPlayingArtwork.test.js
/**
 * The two cover rules shared by AudioPlayer, AudioPlayerFull and the
 * screensaver: which URL is the cover, and what fills the slot when there is
 * none.
 *
 * Only the branches that decide something are asserted — handing the helper an
 * artwork URL and checking it comes back would assert the language, not the
 * rule. What matters is that the no-cover answer is *per source*: the generated
 * station avatar belongs to radio alone (a global answer draws a receiver's
 * track title as a generated avatar), the bundled disc and
 * microphone belong to the sources that ship them, and everyone else falls to
 * their own glyph.
 */
import { describe, it, expect } from 'vitest';
import { nowPlayingArtwork, nowPlayingArtworkPending, artworkFallback } from '@/utils/nowPlayingArtwork';
import { makeAudioState, makeSession } from '../helpers/audioState';

describe('nowPlayingArtwork', () => {
  it('reports no cover as the empty string, not as null', () => {
    // '' is what routes the caller into artworkFallback below; null would
    // render an <img> with no src, which reads as a broken image. The wire
    // publishes a missing cover as null, never as ''.
    expect(nowPlayingArtwork(makeSession({ title: 'Says', artist: 'Nils Frahm' }))).toBe('');
  });

  it('survives the record being absent entirely', () => {
    // The screensaver reads this while a source switches, when there is no
    // session and no resume — a throw there blanks the whole screen.
    expect(nowPlayingArtwork(null)).toBe('');
  });
});

describe('nowPlayingArtworkPending', () => {
  const cd = (details) => makeAudioState({
    source: 'cd',
    details: { kind: 'cd', disc: null, current_track: null, artwork_pending: false, ...details },
  });

  it('follows the flag the CD announces a cover in flight with', () => {
    expect(nowPlayingArtworkPending(cd({ artwork_pending: true }))).toBe(true);
    expect(nowPlayingArtworkPending(cd({ artwork_pending: false }))).toBe(false);
  });

  it('is false for every source that announces nothing', () => {
    // Only the announcer ever lifts it, so reading anything else as pending
    // would veil a placeholder forever.
    expect(nowPlayingArtworkPending(makeAudioState({ source: 'spotify', session: makeSession() }))).toBe(false);
    expect(nowPlayingArtworkPending(makeAudioState({
      source: 'airplay', details: { kind: 'airplay', artwork_width: null },
    }))).toBe(false);
    expect(nowPlayingArtworkPending(null)).toBe(false);
  });
});

describe('artworkFallback', () => {
  it('gives the generated station avatar to radio and to nothing else', () => {
    expect(artworkFallback('radio')).toEqual({ kind: 'avatar' });

    for (const source of ['qobuz', 'bluetooth', 'airplay', 'spotify', 'tidal', 'mac']) {
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
    for (const source of ['spotify', 'tidal', 'bluetooth', 'airplay', 'qobuz', 'mac']) {
      expect(artworkFallback(source)).toEqual({ kind: 'glyph' });
    }
  });
});
