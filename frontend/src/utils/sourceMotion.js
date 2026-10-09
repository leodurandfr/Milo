import { logger } from '@/services/logger';
import { transitionTiming } from '@/utils/transitionTiming';

// The rise a source swap gives the parts a view marks `.source-motion` — its
// content, never the box that clips it — played as one transform animation
// per part on the compositor: up from --source-rise-from on the spring as a
// view comes in, on to --source-rise-to as it leaves. The swap's transition
// itself only fades the slot (`audio-content`, design-system.css); these are
// its hooks. They were a class and an inherited custom property switched at the
// slot, which restyled the whole page under it at every step of every swap.
//
// A rise still under way when its view leaves goes on from where it had got
// to: restarted from its start, it jumped (the status card re-keys on its own
// wording mid-rise). One already rising is left to finish rather than started
// again. A covered view (useCover) is skipped: it rises when uncovered.
//
// In a swap the parts hold their start until Vue's next frame, where the
// slot's own transition starts (its `-to` class), so the rise keeps time with
// the fade as the CSS transition it replaces did — and a part that renders
// during the first of those frames (a source's async chunk resolving) still
// rises, as it did while the slot's `-from` class was on.

const motions = new WeakMap();
// Views whose swap out has started: nothing rises in there any more.
const leaving = new WeakSet();

// The rise's ends. They are in --space-06, which the phone's layout makes
// smaller, so they are read again after a resize (a rotation) rather than kept
// for good — and otherwise once, since a read of the root's style at the start
// of a swap can lay the new view out ahead of its frame.
const ends = new Map();
const warned = new Set();
if (typeof window !== 'undefined') window.addEventListener('resize', () => ends.clear(), { passive: true });

function end(name) {
  if (!ends.has(name)) ends.set(name, getComputedStyle(document.documentElement).getPropertyValue(name).trim());
  const value = ends.get(name);
  if (!value && !warned.has(name)) {
    warned.add(name);
    logger.warn('ui', `Source motion not played: ${name} unreadable`);
  }
  return value;
}

const moving = (animation) => animation?.playState === 'running' || animation?.playState === 'paused';

const parts = (root) => [...root.querySelectorAll('.source-motion')].filter((el) => !el.closest('.is-covered'));

// Starts the animations, or creates them paused (`held`) for playHeld. Each
// new one is created before the one it replaces is cancelled: a browser that
// cannot play it (no `linear()` easing) leaves the old one to finish.
function animate(steps, timing, held) {
  const started = [];
  for (const [el, keyframes] of steps) {
    let animation;
    try {
      animation = el.animate(keyframes, timing);
    } catch (error) {
      if (!(error instanceof TypeError)) throw error;
      return started;
    }
    motions.get(el)?.cancel();
    motions.set(el, animation);
    if (held) animation.pause();
    started.push(animation);
  }
  return started;
}

function playHeld(animations) {
  for (const animation of animations) {
    if (animation.playState !== 'paused') continue;
    // Gone already (a burst of swaps): nothing to move.
    if (animation.effect?.target?.isConnected) animation.play();
    else animation.cancel();
  }
}

// The parts not moving yet rise in, from `from`.
function rising(root, from) {
  return parts(root)
    .filter((el) => !moving(motions.get(el)))
    .map((el) => [el, [{ transform: from }, { transform: 'none' }]]);
}

// Every part heads for `to` from where it is now — its own transform while a
// rise is under way. All read before any animation starts: a read of the
// style after a write lays the page out again, once per part.
function heading(root, to) {
  return parts(root)
    .map((el) => [el, moving(motions.get(el)) ? getComputedStyle(el).transform : 'none'])
    .map(([el, from]) => [el, [{ transform: from }, { transform: to }]]);
}

/** The parts of an uncovered view rise into place at once (useCover). */
export function riseIn(root) {
  const timing = transitionTiming('--transition-spring');
  const from = end('--source-rise-from');
  if (timing && from) animate(rising(root, from), timing, false);
}

/** @enter of the `audio-content` swap: the parts coming in rise into place. */
export function swapIn(root) {
  const timing = transitionTiming('--transition-spring');
  const from = end('--source-rise-from');
  if (!timing || !from) return;
  const held = animate(rising(root, from), timing, true);
  // Vue's nextFrame: the parts rendered in its first frame join the others,
  // unless the view is already on its way out.
  requestAnimationFrame(() => {
    if (!leaving.has(root)) held.push(...animate(rising(root, from), timing, true));
    requestAnimationFrame(() => playHeld(held));
  });
}

/** @leave of the `audio-content` swap: the parts leaving rise away, held there until gone. */
export function swapOut(root) {
  leaving.add(root);
  const timing = transitionTiming('--transition-fast-leave');
  const to = end('--source-rise-to');
  if (!timing || !to) return;
  const held = animate(heading(root, to), { ...timing, fill: 'forwards' }, true);
  requestAnimationFrame(() => requestAnimationFrame(() => playHeld(held)));
}
