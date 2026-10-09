// frontend/tests/architecture/sourceMotionEnvelope.test.js
/**
 * A source swap's rise is a Web Animation (utils/sourceMotion) that Vue cannot
 * see: Vue times the swap off the slot's own CSS transition, so each slot rule
 * declares a `transform` it never animates, as the envelope that keeps the
 * leaving view mounted — and its classes on — until the rise is over.
 *
 * What breaks if the two drift, silently: an envelope shorter than the token
 * the rise plays on unmounts the view mid-motion (a cut); a longer one keeps a
 * view that has finished moving. Mounts nothing: reads the sources.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { stripComments } from '../helpers/stripComments';

const SRC = resolve(dirname(fileURLToPath(import.meta.url)), '../../src');
const read = (path) => stripComments(readFileSync(resolve(SRC, path), 'utf8'));

const motion = read('utils/sourceMotion.js');
const SWAPPING = [
  'components/audio/AudioSourceView.vue',
  'components/audio/BrowserSourceViews.vue',
  'components/gallery/SourceStage.vue',
];
const styles = [
  read('assets/styles/design-system.css'),
  read('components/audio/AudioSourceView.vue'),
  read('components/audio/BrowserSourceViews.vue'),
].join('\n');

/** The token the named export plays on: `transitionTiming('--transition-…')` in its body. */
function tokenOf(name) {
  const body = new RegExp(`export function ${name}\\b[\\s\\S]*?\\n}`).exec(motion)?.[0] ?? '';
  return /transitionTiming\('(--transition-[\w-]+)'\)/.exec(body)?.[1] ?? null;
}

/** The `transform` durations declared by every rule whose selector ends on `selectorEnd`. */
function envelopes(selectorEnd) {
  return [...styles.matchAll(/([^{}]+)\{([^{}]*)\}/g)]
    .filter(([, selector]) => selector.trim().endsWith(selectorEnd))
    .flatMap(([, , body]) => [...body.matchAll(/transform\s+var\((--transition-[\w-]+)\)/g)].map((m) => m[1]));
}

describe('the envelope of a source swap\'s rise', () => {
  it('reads the hooks and the rules it compares', () => {
    expect(tokenOf('swapIn')).not.toBeNull();
    expect(tokenOf('swapOut')).not.toBeNull();
    expect(envelopes('.audio-content-enter-active').length).toBeGreaterThanOrEqual(3);
    expect(envelopes('.audio-content-leave-active').length).toBeGreaterThanOrEqual(1);
  });

  it('plays the rise in every audio-content swap', () => {
    // The rise used to come with the transition's name, from CSS; now each
    // swap binds the hooks, and one that forgets them loses it in silence.
    // Lyrics only rises in: closing is a plain fade.
    const swaps = SWAPPING.flatMap((file) => [...read(file).matchAll(/<Transition\b([^>]*name="audio-content"[^>]*)>/g)]
      .map((m) => ({ file, attrs: m[1] })));
    expect(swaps.length).toBeGreaterThanOrEqual(4);
    for (const { file, attrs } of swaps) {
      expect(attrs, file).toMatch(/@enter="swapIn"/);
      const lyrics = file.endsWith('AudioSourceView.vue') && /onOverlayEntered/.test(attrs);
      if (lyrics) expect(attrs, file).not.toMatch(/@leave=/);
      else expect(attrs, file).toMatch(/@leave="swapOut"/);
    }
  });

  it('keeps an entering view as long as its rise in plays', () => {
    expect(new Set(envelopes('.audio-content-enter-active'))).toEqual(new Set([tokenOf('swapIn')]));
  });

  it('keeps a leaving view as long as its rise out plays', () => {
    expect(new Set(envelopes('.audio-content-leave-active'))).toEqual(new Set([tokenOf('swapOut')]));
  });
});
