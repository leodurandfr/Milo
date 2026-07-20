// frontend/src/composables/useScreensaverReveal.js
// Replays a source view's entrance animation when the audio screensaver is
// dismissed. MainView provides a "reveal nonce" that useScreensaver bumps on
// every close; a revealed view re-animates in one of two ways:
//   - useScreensaverRevealNonce() → bind into a subtree's :key to remount it
//     (use when the target already owns an entrance animation, e.g. a stagger).
//   - useScreensaverRevealPulse() → a boolean to toggle the global
//     `.screensaver-revealing` class (use when the target has no animation yet).
import { inject, ref, watch } from 'vue';
import { useTimer } from '@/composables/useTimer';

/** Injection key for the screensaver reveal nonce (provided by MainView). */
export const SCREENSAVER_REVEAL_NONCE = 'screensaverRevealNonce';

/** Raw reveal nonce — increments on each screensaver close. */
export function useScreensaverRevealNonce() {
  return inject(SCREENSAVER_REVEAL_NONCE, ref(0));
}

/**
 * Pulses `true` for one entrance-animation cycle each time the screensaver
 * closes, so the revealed view can replay its entrance. Never pulses on mount
 * (watcher, not immediate) — so a genuine source-open animation isn't doubled.
 *
 * @param {number} [durationMs=1400] How long the class stays on (≈ the spring
 *   entrance length); the animation itself is defined by `.screensaver-revealing`.
 * @returns {import('vue').Ref<boolean>}
 */
export function useScreensaverRevealPulse(durationMs = 1400) {
  const nonce = useScreensaverRevealNonce();
  const timer = useTimer();
  const revealing = ref(false);
  let handle = null;

  watch(nonce, () => {
    if (handle) timer.clear(handle);
    revealing.value = true;
    handle = timer.setTimeout(() => {
      revealing.value = false;
      handle = null;
    }, durationMs);
  });

  return revealing;
}
