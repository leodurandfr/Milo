// frontend/tests/architecture/spotifyShelfEstimate.test.js
/**
 * A Spotify shelf not drawn yet (content-visibility: auto) holds the height
 * SpotifyShelfRow works out for its cards: a square cover, then the card's
 * gap, a heading-4 name, and, when a card has one, the info gap and a
 * text-mono-medium byline. That height restates SpotifyCard's box.
 *
 * What breaks if it drifts, silently: every row not drawn yet is the wrong
 * height, so the page below jumps as rows draw, and a scroll restored on back
 * lands on another shelf. Mounts nothing: it reads the two sources the browser
 * reads.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const SRC = resolve(dirname(fileURLToPath(import.meta.url)), '../../src');
const read = (path) => readFileSync(resolve(SRC, path), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');

const card = read('components/spotify/cards/SpotifyCard.vue');
const row = read('components/spotify/SpotifyShelfRow.vue');

/** The declarations of the first rule whose selector is exactly `selector`. */
function rule(source, selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return new RegExp(`(?:^|\\n|\\})\\s*${escaped}\\s*\\{([^}]*)\\}`).exec(source)?.[1] ?? null;
}

describe('the height a Spotify shelf reserves', () => {
  it('reads the rules it compares', () => {
    expect(rule(card, '.playlist-card')).not.toBeNull();
    expect(rule(card, '.playlist-info')).not.toBeNull();
    expect(rule(row, '.shelf-row')).not.toBeNull();
    expect(rule(row, '.shelf-row.with-byline')).not.toBeNull();
  });

  it('is the card: a square cover, then its gap and its name', () => {
    expect(rule(card, '.playlist-cover')).toMatch(/aspect-ratio:\s*1\s*;/);
    expect(rule(card, '.playlist-card')).toMatch(/gap:\s*var\(--space-02\)/);
    expect(card).toMatch(/class="playlist-name heading-4"/);
    const estimate = rule(row, '.shelf-row');
    expect(estimate).toMatch(/--shelf-card-height:[\s\S]*\+ var\(--space-02\) \+ var\(--line-height-h4\)\s*\)/);
  });

  it('adds the byline line the card draws under its name', () => {
    expect(rule(card, '.playlist-info')).toMatch(/gap:\s*var\(--space-01\)/);
    expect(card).toMatch(/v-if="byline" class="playlist-owner text-mono-medium"/);
    expect(rule(row, '.shelf-row.with-byline'))
      .toMatch(/calc\(var\(--shelf-card-height\) \+ var\(--space-01\) \+ var\(--line-height-mono-medium\)\)/);
  });
});
