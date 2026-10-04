// frontend/tests/architecture/artworkParity.test.js
/**
 * The full player and the playing bar must decide the cover the same way.
 *
 * The rule was once restated per source and per view, and that is exactly how
 * Bluetooth ended up publishing a resolved cover that one view drew and another
 * replaced with a generated text avatar. So the rule now lives once, in
 * utils/nowPlayingArtwork (and the held-cover transition in
 * useArtworkTransition), and this asserts every consumer still goes through it —
 * the failure mode being silent and visual, the kind CI cannot see and a
 * mounted-component test would not catch either (it would assert markup, which
 * this suite deliberately does not do).
 *
 * Two halves: *which URL is the cover* (AudioPlayerFull, which reads it from
 * the now-playing record; the playing bar is handed its cover by the source),
 * and *what fills the slot when there is no cover*, which both views answer for
 * every source there is. The second is what would draw a receiver's track title
 * as a generated avatar.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';
import { stripComments } from '../helpers/stripComments.js';

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC_DIR = resolve(HERE, '../../src');

const player = readFileSync(join(SRC_DIR, 'components/audio/AudioPlayerFull.vue'), 'utf8');
const browserPlayer = readFileSync(join(SRC_DIR, 'components/audio/AudioPlayer.vue'), 'utf8');
const transition = readFileSync(join(SRC_DIR, 'composables/useArtworkTransition.js'), 'utf8');

// Every rule below that asserts a *name is absent* reads these, not the raw
// files. Writing `// The helper owns album_art_url; do not read it here.` at the
// right place in a view used to turn this file red — documenting a rule where
// it applies is the most natural thing a reader can do, and a red nobody
// believes is worse than no rule. Measured, twice.
const playerCode = stripComments(player);
const browserPlayerCode = stripComments(browserPlayer);

describe('artwork parity between the full player and the playing bar', () => {
  it('extracts a plausible surface first', () => {
    // A rename that emptied either file would otherwise make every assertion
    // below pass on nothing.
    expect(player).toMatch(/artwork-container/);
    expect(browserPlayer).toMatch(/player-artwork/);
    expect(transition).toMatch(/export function useArtworkTransition/);
  });

  it('derives the cover from the one shared helper, never from a record field', () => {
    // `.artwork` on a session, a resume or the player's snapshot is what the
    // helper reads; the view reading it itself is a cover expression of its own.
    expect(player).toMatch(/nowPlayingArtwork\(/);
    expect(playerCode).not.toMatch(/(nowPlaying|session|resume|Metadata\.value)\??\.artwork/);
  });

  it('runs the held-cover transition, announced wait included', () => {
    // CD veils its placeholder while the jacket is fetched; a player left
    // without the signal shows the bare placeholder and swaps it a moment later.
    expect(playerCode).toMatch(/useArtworkTransition\(\s*\w+,\s*\w+,\s*artworkAnnounced\s*\)/);
    expect(playerCode).toMatch(/nowPlayingArtworkPending\(/);

    // And it may not re-roll its own wait: the bounded timeout is the only
    // thing that lifts a veil when a cover never arrives.
    expect(playerCode).not.toMatch(/setTimeout/);
  });

  it('leaves to the composable what counts as a cover', () => {
    // A 1×1 tracking pixel (or a broken favicon) decodes perfectly well and is
    // not a cover. The size rule lives in the composable, and the player wires
    // its handlers straight to the preloader's load/error.
    //
    // Asserted on the template attribute, not on the identifier: a file that
    // destructures `settleFromLoad` and then hands @load its own handler still
    // mentions the name everywhere, so matching the name alone stays green
    // through exactly the regression this guards.
    expect(transition).toMatch(/MIN_IMAGE_SIZE/);
    expect(playerCode).toMatch(/@load="settleFromLoad"/);
    expect(playerCode).toMatch(/@error="settleFromError"/);
    expect(playerCode).not.toMatch(/MIN_IMAGE_SIZE/);
  });

  it('lets no view own a placeholder image', () => {
    // A placeholder imported separately by each file is as many chances to pick
    // a different image for the same silence. The helper owns the choice; a view
    // only renders what it is handed, so none of them may reach for an asset or
    // for the constants module behind the helper's back.
    for (const view of [playerCode, browserPlayerCode]) {
      expect(view).not.toMatch(/from '@\/assets\//);
      expect(view).not.toMatch(/constants\/placeholders/);
    }
  });

  it('resolves the no-cover fallback through the same helper in both views', () => {
    // Asserted on the import as well as the call: a view that shadows the name
    // with a local `const artworkFallback = …` still mentions it everywhere, so
    // matching the call alone stays green through the regression. Measured — it
    // did, on the first version of this assertion.
    for (const view of [playerCode, browserPlayerCode]) {
      expect(view).toMatch(/import \{[^}]*artworkFallback[^}]*\} from '@\/utils\/nowPlayingArtwork'/);
      expect(view).toMatch(/artworkFallback\(/);
    }

    // And the generated avatar is reachable from that verdict only. Matching the
    // import alone would stay green through exactly the regression this guards,
    // since the fixed code imports it too — for radio.
    expect(playerCode).toMatch(/kind !== 'avatar'/);
    expect(browserPlayerCode).toMatch(/kind === 'avatar'/);
  });
});
