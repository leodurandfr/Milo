// frontend/src/composables/useDelayedFlag.js
// A wait indicator that rises only once the wait is long enough to notice.
//
// Every track change passes through `loading` for a few hundred milliseconds,
// and a spinner bound straight to it flashed in place of the play/pause glyph
// on each skip. Under about a second a wait does not break the flow and needs
// no indicator; past it the user wants to know something is still happening (a
// CD spinning up, a radio tuning). The flag drops at once: the wait is over.
import { ref, watch } from 'vue';
import { useTimer } from '@/composables/useTimer';

export const WAIT_INDICATOR_DELAY_MS = 1000;

/**
 * @param {import('vue').WatchSource<boolean>} source - the wait itself
 * @param {number} [delayMs]
 * @returns {import('vue').Ref<boolean>} true once `source` has held for `delayMs`
 */
export function useDelayedFlag(source, delayMs = WAIT_INDICATOR_DELAY_MS) {
  const timer = useTimer();
  const shown = ref(false);
  let pending = null;

  watch(
    source,
    (waiting) => {
      if (pending) {
        timer.clear(pending);
        pending = null;
      }
      if (!waiting) {
        shown.value = false;
        return;
      }
      pending = timer.setTimeout(() => {
        pending = null;
        shown.value = true;
      }, delayMs);
    },
    { immediate: true }
  );

  return shown;
}
