// frontend/tests/pure/playerLines.test.js
/**
 * What the players say around the title, from a source's details — and, of
 * that, whether the playing bar's card draws the source bar.
 *
 * The card draws it for one case only: a station playing a detected song with
 * a title, an artist and a cover of its own, whose card cover is then the
 * song's and says nothing of the station. A song without its own cover leaves
 * the station's logo in the cover slot, and the bar would draw it twice.
 * Every other card names its source already, and a source bar there would be
 * a line of text the card has no height to spare for. The full player draws it always
 * (not decided here).
 *
 * Goes red if a card case starts or stops drawing the source bar, or if the
 * station's label and picture stop travelling with the detection.
 */
import { describe, it, expect } from 'vitest';
import { linesOf } from '@/composables/usePlayerMetadata';

const STATION = { name: 'Radio Nova', favicon: 'https://nova.fr/logo.png' };
const SONG = { title: 'Says', artist: 'Nils Frahm', artwork: 'https://cdn/says.jpg' };

describe('the source bar on the playing bar’s card', () => {
  it('shows a station playing a detected song, with its name and its logo', () => {
    const lines = linesOf({ kind: 'radio', station: STATION, track: SONG });
    expect(lines.barOnCard).toBe(true);
    expect(lines.barLabel).toBe(STATION.name);
    expect(lines.barImage).toContain(encodeURIComponent(STATION.favicon));
  });

  it('shows a station with no logo by its generated avatar (an empty picture)', () => {
    const lines = linesOf({ kind: 'radio', station: { name: 'FIP', favicon: '' }, track: SONG });
    expect(lines.barOnCard).toBe(true);
    expect(lines.barImage).toBe('');
  });

  it('shows where another device plays, since nothing else on the card says it is not here', () => {
    const lines = linesOf({ kind: 'spotify', account: 'owner', remote: { device_name: 'iPhone', paused: true } });
    expect(lines.barOnCard).toBe(true);
    expect(lines.barRemote).toEqual({ device: 'iPhone', paused: true });
  });

  it.each([
    ['a station with no song detected', { kind: 'radio', station: STATION, track: null }],
    ['a song detected with no cover', { kind: 'radio', station: STATION, track: { ...SONG, artwork: null } }],
    ['a song detected with no artist', { kind: 'radio', station: STATION, track: { ...SONG, artist: null } }],
    ['an episode with the show’s picture', {
      kind: 'podcast',
      episode: { image_url: 'https://cdn/show.jpg', podcast: { name: 'Show', image_url: 'https://cdn/show.jpg' } }
    }],
    ['an episode with a picture of its own', {
      kind: 'podcast',
      episode: { image_url: 'https://cdn/episode.jpg', podcast: { name: 'Show', image_url: 'https://cdn/show.jpg' } }
    }],
    ['a Music Library queue', { kind: 'music_library', queue: [], queue_index: 0 }],
    ['a Spotify account', { kind: 'spotify', account: 'owner' }],
    ['no details at all', null]
  ])('leaves it out for %s', (_name, details) => {
    expect(linesOf(details).barOnCard).toBe(false);
  });

  it('does not name the station beside its own logo on the full player either', () => {
    const lines = linesOf({ kind: 'radio', station: STATION, track: { ...SONG, artwork: null } });
    expect(lines.barLabel).toBeNull();
    expect(lines.barImage).toBeNull();
  });
});
