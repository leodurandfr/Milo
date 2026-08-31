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
 * So the rule now lives once, in utils/nowPlayingArtwork, and this asserts every
 * consumer still goes through it — the failure mode being silent and visual, the
 * kind CI cannot see and a mounted-component test would not catch either (it
 * would assert markup, which this suite deliberately does not do).
 *
 * Two halves, and the second was added after the first had been green for
 * months while the views disagreed anyway: *which URL is the cover* (scoped to
 * the sources whose artwork rides on `systemState.metadata` — the three browser
 * sources read their own Pinia store in both places, already a single source of
 * truth), and *what fills the slot when there is no cover*, which is every
 * source there is. The second is what let a DLNA renderer be announced
 * full-screen as the word "DLNA" in a coloured tile while the player behind it
 * showed the DLNA glyph.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';
import { stripComments } from '../helpers/stripComments.js';

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC_DIR = resolve(HERE, '../../src');

const screensaver = readFileSync(join(SRC_DIR, 'composables/useScreensaver.js'), 'utf8');
const player = readFileSync(join(SRC_DIR, 'components/audio/AudioPlayerFull.vue'), 'utf8');
const screensaverView = readFileSync(join(SRC_DIR, 'components/audio/AudioScreensaver.vue'), 'utf8');
const browserPlayer = readFileSync(join(SRC_DIR, 'components/audio/AudioPlayer.vue'), 'utf8');
const richDisplay = readFileSync(join(SRC_DIR, 'composables/useRichDisplay.js'), 'utf8');
const transition = readFileSync(join(SRC_DIR, 'composables/useArtworkTransition.js'), 'utf8');

// Every rule below that asserts a *name is absent* reads these, not the raw
// files. Writing `// The helper owns album_art_url; do not read it here.` at the
// right place in useScreensaver.js used to turn this file red — documenting a
// rule where it applies is the most natural thing a reader can do, and a red
// nobody believes is worse than no rule. Measured, twice.
const screensaverCode = stripComments(screensaver);
const playerCode = stripComments(player);
const screensaverViewCode = stripComments(screensaverView);
const browserPlayerCode = stripComments(browserPlayer);

describe('artwork parity between the player and the screensaver', () => {
  it('extracts a plausible surface first', () => {
    // A rename that emptied either file would otherwise make every assertion
    // below pass on nothing.
    expect(screensaver).toMatch(/screensaverData\s*=\s*computed/);
    expect(screensaver).toMatch(/function mediaData\(/);
    expect(screensaver).toMatch(/function simpleData\(/);
    expect(player).toMatch(/artwork-container/);
    expect(transition).toMatch(/export function useArtworkTransition/);
    expect(richDisplay).toMatch(/export function useRichDisplay/);
  });

  it('derives the cover from the one shared helper on both sides', () => {
    expect(screensaver).toMatch(/nowPlayingArtwork\(/);
    expect(player).toMatch(/nowPlayingArtwork\(/);
  });

  it('never reads album_art_url in the screensaver — that field is the helper\'s', () => {
    // Only asserted on the screensaver: the player legitimately names the field
    // once, caching the raw metadata so its last-valid copy is still something
    // the helper can read.
    expect(screensaverCode).not.toMatch(/album_art_url/);
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
      'nowPlayingArtwork(metadata)',
      'track.artwork || stationArt',
      'stationArt',
      'episode?.image_url || null',
      'track?.albumArtUrl || null',
    ];

    // Per line, minus the trailing comma — the expressions contain commas of
    // their own (`nowPlayingArtwork(source, metadata)`).
    const found = [...screensaver.matchAll(/^\s*artwork:\s*(.+?),?\s*$/gm)].map((m) => m[1]);

    // The extractor must find a real surface, or every assertion below is vacuous.
    // Six, not one per source: the four receivers share a single `receiver()`
    // helper, so they contribute one expression between them. That is stricter
    // than four identical lines, not looser — a fifth receiver cannot introduce
    // a cover expression of its own without leaving this branch, and leaving it
    // means a new line here for a reviewer to see.
    expect(found.length).toBeGreaterThanOrEqual(6);
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
    expect(playerCode).not.toMatch(/setTimeout/);
    expect(screensaverViewCode).not.toMatch(/setTimeout/);
  });

  it('lets neither decide on its own what counts as a cover', () => {
    // The same cover and the same arrival are not enough — the two views also
    // have to accept and reject the same images. They did not: the player
    // promoted anything that decoded while the screensaver rejected anything
    // under MIN_IMAGE_SIZE, so a 1×1 tracking pixel (DLNA senders push them,
    // and a broken favicon behaves the same) drew a cover in one view and a
    // generated avatar in the other. Identical URL, opposite verdicts — which
    // the URL-parity assertions above cannot see, and which shows up precisely
    // during the leave crossfade, when the two are superimposed.
    //
    // So the size rule lives in the composable, and both sides wire its
    // handlers straight to the preloader's load/error.
    //
    // Asserted on the template attribute, not on the identifier: a file that
    // destructures `settleFromLoad` and then hands @load its own handler still
    // mentions the name everywhere, so matching the name alone stays green
    // through exactly the regression this guards.
    expect(transition).toMatch(/MIN_IMAGE_SIZE/);
    for (const view of [playerCode, screensaverViewCode]) {
      expect(view).toMatch(/@load="settleFromLoad"/);
      expect(view).toMatch(/@error="settleFromError"/);
      expect(view).not.toMatch(/MIN_IMAGE_SIZE/);
    }
  });

  it('lets no view own a placeholder image', () => {
    // A placeholder imported separately by each file is as many chances to pick
    // a different image for the same silence. The helper owns the choice; a view
    // only renders what it is handed, so none of them may reach for an asset or
    // for the constants module behind the helper's back.
    expect(screensaverCode).not.toMatch(/placeholder/);
    for (const view of [playerCode, screensaverViewCode, browserPlayerCode]) {
      expect(view).not.toMatch(/from '@\/assets\//);
      expect(view).not.toMatch(/constants\/placeholders/);
    }
  });

  it('resolves the no-cover fallback through the same helper in all three views', () => {
    // The half the URL assertions above cannot see. Both views called
    // nowPlayingArtwork and were still showing different things the moment it
    // answered '': the player painted its source glyph, the screensaver painted
    // a text avatar generated from whatever string was in `title` — an episode
    // name, a track name, a phone's name, or the literal "DLNA".
    // Asserted on the import as well as the call: a view that shadows the name
    // with a local `const artworkFallback = …` still mentions it everywhere, so
    // matching the call alone stays green through the regression. Measured — it
    // did, on the first version of this assertion.
    for (const view of [playerCode, screensaverViewCode, browserPlayerCode]) {
      expect(view).toMatch(/import \{[^}]*artworkFallback[^}]*\} from '@\/utils\/nowPlayingArtwork'/);
      expect(view).toMatch(/artworkFallback\(/);
    }

    // And the generated avatar is reachable from that verdict only. Matching the
    // import alone would stay green through exactly the regression this guards,
    // since the fixed code imports it too — for radio.
    expect(screensaverViewCode).toMatch(/kind !== 'avatar'/);
    expect(browserPlayerCode).toMatch(/kind === 'avatar'/);
  });

  it('takes the screensaver layout from useRichDisplay instead of deciding again', () => {
    // Media card or status card is already answered, once, for the view sitting
    // behind the overlay. AirPlay and Bluetooth used to restate that rule here
    // verbatim and DLNA never restated it at all — so DLNA drew a full media
    // card over a status card that had refused it for want of a cover.
    expect(screensaver).toMatch(/useRichDisplay\(\)/);
    expect(screensaver).toMatch(/richSource\.value === null \? simpleData/);

    // No second gate: the quality threshold that decides a rich view belongs to
    // useRichDisplay, and a copy here is the drift itself.
    expect(screensaverCode).not.toMatch(/UNTRUSTED_SENDER_MIN_ARTWORK_PX/);
    expect(richDisplay).toMatch(/UNTRUSTED_SENDER_MIN_ARTWORK_PX/);
  });
});
