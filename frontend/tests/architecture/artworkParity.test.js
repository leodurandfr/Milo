// frontend/tests/architecture/artworkParity.test.js
/**
 * The screensaver and the player must show the same cover.
 *
 * The screensaver is a full-screen restatement of the now-playing view, and its
 * leave animation crossfades into it — two different covers there do not read as
 * two views, they read as a glitch. The rule was previously restated in every
 * branch of useScreensaver's display-data computation, and that is exactly how
 * Bluetooth ended up publishing a resolved cover that the player drew and the
 * screensaver replaced with a generated text avatar.
 *
 * So the rule now lives once, in utils/nowPlayingArtwork, and this asserts both
 * consumers still go through it — the failure mode being silent and visual, the
 * kind CI cannot see and a mounted-component test would not catch either (it
 * would assert markup, which this suite deliberately does not do).
 *
 * Scope: the sources whose artwork rides on `systemState.metadata`. The three
 * browser sources read their own Pinia store in both places, which is already a
 * single source of truth.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC_DIR = resolve(HERE, '../../src');

const screensaver = readFileSync(join(SRC_DIR, 'composables/useScreensaver.js'), 'utf8');
const player = readFileSync(join(SRC_DIR, 'components/audio/AudioPlayerFull.vue'), 'utf8');
const screensaverView = readFileSync(join(SRC_DIR, 'components/audio/AudioScreensaver.vue'), 'utf8');

describe('artwork parity between the player and the screensaver', () => {
  it('extracts a plausible surface first', () => {
    // A rename that emptied either file would otherwise make every assertion
    // below pass on nothing.
    expect(screensaver).toMatch(/screensaverData\s*=\s*computed/);
    expect(player).toMatch(/artwork-container/);
  });

  it('derives the cover from the one shared helper on both sides', () => {
    expect(screensaver).toMatch(/nowPlayingArtwork\(/);
    expect(player).toMatch(/nowPlayingArtwork\(/);
  });

  it('never reads album_art_url in the screensaver — that field is the helper\'s', () => {
    // Only asserted on the screensaver: the player legitimately names the field
    // once, caching the raw metadata so its last-valid copy is still something
    // the helper can read.
    expect(screensaver).not.toMatch(/album_art_url/);
  });

  it('pins every cover the screensaver can show', () => {
    // The real guard, and the one the previous two miss: the Bluetooth bug was
    // `artwork: null` written straight into a branch — no album_art_url to
    // detect, and the other branches still called the helper, so nothing else
    // here would have gone red. Pinning the whole set means a new source cannot
    // invent a cover expression without a reviewer seeing it.
    //
    // The three store-driven ones are deliberate: radio, podcast and the music
    // library read the same Pinia store their player does, which is already a
    // single source of truth. Everything else must go through the helper.
    const ALLOWED = [
      'nowPlayingArtwork(source, metadata)',
      'track.artwork || stationArt',
      'stationArt',
      'episode?.image_url || null',
      'track?.albumArtUrl || null',
    ];

    // Per line, minus the trailing comma — the expressions contain commas of
    // their own (`nowPlayingArtwork(source, metadata)`).
    const found = [...screensaver.matchAll(/^\s*artwork:\s*(.+?),?\s*$/gm)].map((m) => m[1]);

    // The extractor must find a real surface, or every assertion below is vacuous.
    expect(found.length).toBeGreaterThanOrEqual(8);
    for (const expression of found) {
      expect(ALLOWED).toContain(expression);
    }
  });

  it('runs the same held-cover transition on both sides', () => {
    // Not just the same cover — the same *arrival*. The screensaver is
    // superimposed on the player during its leave crossfade, so a track change
    // that veiled in one view and flashed a fallback in the other would be
    // visible exactly at the moment the two overlap.
    expect(player).toMatch(/useArtworkTransition\(/);
    expect(screensaverView).toMatch(/useArtworkTransition\(/);

    // And neither may re-roll its own wait: the bounded timeout is the only
    // thing that lifts a veil when a cover never arrives.
    expect(player).not.toMatch(/setTimeout/);
    expect(screensaverView).not.toMatch(/setTimeout/);
  });

  it('lets neither own a placeholder image', () => {
    // A placeholder imported separately by each file is two chances to pick a
    // different image for the same silence.
    expect(screensaver).not.toMatch(/placeholder/);
    expect(player).not.toMatch(/from '@\/assets\//);
  });
});
