// frontend/tests/pure/nowPlayingArtwork.test.js
/**
 * The two cover rules shared by AudioPlayer and AudioPlayerFull: which URL is
 * the cover, and what fills the slot when there is none.
 *
 * Only the branches that decide something are asserted — handing the helper an
 * artwork URL and checking it comes back would assert the language, not the
 * rule. What matters is that the no-cover answer is *per source*: the generated
 * station avatar belongs to radio alone (a global answer draws a receiver's
 * track title as a generated avatar), the microphone to podcasts, and the
 * disc to everything else — never the source's own icon, which the source bar
 * under the slot already draws.
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
    // A source switching has no session and no resume to hand over — a throw
    // there blanks the whole screen.
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

  it('reads the same flag whatever source announces it', () => {
    // Bluetooth and radio look a cover up from the track text after the track
    // is published; one rule for every kind, so a source that starts announcing
    // needs no frontend table to be heard.
    const announcing = (source, details) => makeAudioState({ source, session: makeSession(), details });
    expect(nowPlayingArtworkPending(announcing('bluetooth', { kind: 'bluetooth', artwork_pending: true }))).toBe(true);
    expect(nowPlayingArtworkPending(announcing('bluetooth', { kind: 'bluetooth', artwork_pending: false }))).toBe(false);
    expect(nowPlayingArtworkPending(announcing('radio', {
      kind: 'radio', station: { name: 'FIP' }, track: { title: 'Snibor', artist: 'Gil Evans', artwork: null },
      artwork_pending: true,
    }))).toBe(true);
  });

  it('is false for every source that announces nothing', () => {
    // Only the announcer ever lifts it, so reading anything else as pending
    // would hold a cover past its track on every change.
    expect(nowPlayingArtworkPending(makeAudioState({ source: 'spotify', session: makeSession() }))).toBe(false);
    expect(nowPlayingArtworkPending(makeAudioState({
      source: 'airplay', details: { kind: 'airplay', artwork_width: null },
    }))).toBe(false);
    expect(nowPlayingArtworkPending(null)).toBe(false);
  });
});

describe('artworkFallback', () => {
  it('gives the generated station avatar to radio and to nothing else', () => {
    expect(artworkFallback('radio', 'FIP')).toEqual({ kind: 'avatar' });

    for (const source of ['qobuz', 'bluetooth', 'airplay', 'spotify', 'tidal', 'mac']) {
      expect(artworkFallback(source, 'Says').kind).not.toBe('avatar');
    }
  });

  it('gives a station with no name the disc, never an empty slot', () => {
    // An avatar is drawn from a name; without one there is nothing to draw,
    // and an avatar kind with no markup left the full player's slot blank.
    expect(artworkFallback('radio', '')).toEqual(artworkFallback('music_library'));
  });

  it('ships a disc for the musical sources and a microphone for podcasts', () => {
    const music = artworkFallback('music_library');
    const cd = artworkFallback('cd');
    const podcast = artworkFallback('podcast');

    expect(music.kind).toBe('image');
    // CD and the library are the same silence, so they are the same drawing —
    // they were two files, and the two drifted apart in format and in ground.
    expect(cd).toEqual(music);
    // Spotify is drawn by AudioPlayer, which renders an image or an avatar:
    // a glyph there would leave its cover frame empty.
    expect(artworkFallback('spotify')).toEqual(music);

    expect(podcast.kind).toBe('image');
    expect(podcast.src).not.toBe(music.src);
  });

  it('gives the receivers and the connect players the same disc', () => {
    // A track with no cover is the same silence whichever source plays it.
    // Bluetooth is the common case — AVRCP never carries a cover, so every
    // lookup that misses lands here — and its source icon in the slot read as
    // a status card drawn inside a player.
    const music = artworkFallback('music_library');
    for (const source of ['tidal', 'bluetooth', 'airplay', 'qobuz', 'mac']) {
      expect(artworkFallback(source)).toEqual(music);
    }
  });
});
