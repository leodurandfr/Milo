// frontend/tests/pure/playerLines.test.js
/**
 * What the players say around the title, from a source's details — and, of
 * that, whether the playing bar's card draws the source bar.
 *
 * The card draws it for one case only: a station playing a detected song,
 * whose card cover is the song's and says nothing of the station. Every other
 * card names its source already, and a source bar there would be a line of
 * text the card has no height to spare for. The full player draws it always
 * (not decided here).
 *
 * Goes red if a card case starts or stops drawing the source bar, or if the
 * station's label and picture stop travelling with the detection.
 */
import { describe, it, expect } from 'vitest';
import { linesOf } from '@/composables/usePlayerMetadata';

const STATION = { name: 'Radio Nova', favicon: 'https://nova.fr/logo.png' };

describe('the source bar on the playing bar’s card', () => {
  it('shows a station playing a detected song, with its name and its logo', () => {
    const lines = linesOf({ kind: 'radio', station: STATION, track: { title: 'Says', artist: 'Nils Frahm' } });
    expect(lines.barOnCard).toBe(true);
    expect(lines.barLabel).toBe(STATION.name);
    expect(lines.barImage).toContain(encodeURIComponent(STATION.favicon));
  });

  it('shows a station with no logo by its generated avatar (an empty picture)', () => {
    const lines = linesOf({ kind: 'radio', station: { name: 'FIP', favicon: '' }, track: { title: 'Says' } });
    expect(lines.barOnCard).toBe(true);
    expect(lines.barImage).toBe('');
  });

  it.each([
    ['a station with no song detected', { kind: 'radio', station: STATION, track: null }],
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
});
