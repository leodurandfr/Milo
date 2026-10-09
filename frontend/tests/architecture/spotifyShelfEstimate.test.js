// frontend/tests/architecture/spotifyShelfEstimate.test.js
/**
 * A Spotify shelf not drawn yet (content-visibility: auto) holds the height
 * SpotifyShelfRow works out for its cards: a square cover, then the card's
 * gap and what it writes under it (utils/spotifyCard.js::cardLines) — a
 * heading-4 line, and under it, past the info gap, a text-body-small byline.
 * That height restates SpotifyCard's box.
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
    expect(rule(row, '.shelf-row.with-name')).not.toBeNull();
    expect(rule(row, '.shelf-row.with-name.with-byline')).not.toBeNull();
  });

  it('is the card: a square cover alone when nothing is written under it', () => {
    expect(rule(card, '.playlist-cover')).toMatch(/aspect-ratio:\s*1\s*;/);
    expect(card).toMatch(/v-if="lines.heading" class="playlist-info"/);
    expect(rule(row, '.shelf-row')).toMatch(/contain-intrinsic-block-size:\s*var\(--shelf-cover-height\)\s*;/);
  });

  it('adds the card\'s gap and its heading line', () => {
    expect(rule(card, '.playlist-card')).toMatch(/gap:\s*var\(--space-03\)/);
    expect(card).toMatch(/<p class="playlist-name heading-4">\{\{ lines.heading \}\}/);
    expect(row).toMatch(/withName = computed\(\(\) => lines\.value\.some\(\(line\) => line\.heading\)\)/);
    expect(rule(row, '.shelf-row.with-name'))
      .toMatch(/calc\(var\(--shelf-cover-height\) \+ var\(--space-03\) \+ var\(--line-height-h4\)\)/);
  });

  it('adds the byline line under it, past the info gap', () => {
    expect(rule(card, '.playlist-info')).toMatch(/gap:\s*var\(--space-01\)/);
    expect(card).toMatch(/v-if="lines.byline" class="playlist-owner text-body-small"/);
    expect(row).toMatch(/withByline = computed\(\(\) => lines\.value\.some\(\(line\) => line\.byline\)\)/);
    expect(rule(row, '.shelf-row.with-name.with-byline')).toMatch(
      /calc\(\s*var\(--shelf-cover-height\) \+ var\(--space-03\) \+ var\(--line-height-h4\) \+ var\(--space-01\) \+ var\(--line-height-body-small\)\s*\)/,
    );
  });
});
